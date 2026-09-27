#!/usr/bin/env python3
"""Выгрузка источника: AmaSum → parquet-шарды рядом с репозиторием.

Скрипт запускается ОДИН РАЗ и живёт вне DVC-пайплайна намеренно. Он не
принимает ни одного смыслового решения о датасете: качает архив, распаковывает
и перекладывает 25 203 отдельных JSON-файла в несколько parquet-шардов,
сохраняя поля источника как есть. Вся переработка — в стадии collect, где её
видно в метриках.

Почему шарды, а не чтение дерева JSON напрямую из стадии collect:
  * 25 тысяч мелких файлов в deps заставляют DVC хешировать их на каждый
    `dvc status`, и пайплайн начинает думать дольше, чем считать;
  * версии датасета v1/v2 в params.yaml — это список файлов-источников,
    и шард ровно ложится на этот контракт;
  * источник лежит ВНЕ репозитория: 1.3 ГБ сырья не должны даже теоретически
    оказаться рядом с `git add .`.

Использование:
    uv run python scripts/fetch_source.py
    uv run python scripts/fetch_source.py --dest ../../datasets/amasum
"""

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

URL = (
    "https://abrazinskas.s3.eu-west-1.amazonaws.com/downloads/projects/"
    "selsum/data/raw_min_10_max_100_revs.zip"
)
ARCHIVE = "raw_min_10_max_100_revs.zip"
INNER = "min_10_max_100_revs_filt_complete"
# Берём только train-часть авторского разбиения: свой сплит мы всё равно
# делаем сами, по группам, и чужие train/valid/test нам только мешали бы.
SUBSET = "train"
SHARD_ROWS = 5000

SCHEMA = pa.schema(
    [
        ("asin", pa.string()),
        ("title", pa.string()),
        ("categories", pa.list_(pa.string())),
        ("product_rating", pa.float64()),
        ("verdict", pa.string()),
        ("pros", pa.list_(pa.string())),
        ("cons", pa.list_(pa.string())),
        ("summary_source", pa.string()),
        ("reviews", pa.list_(pa.struct([
            ("title", pa.string()),
            ("text", pa.string()),
            ("rating", pa.float64()),
            ("helpful_votes", pa.int64()),
            ("verified", pa.bool_()),
        ]))),
    ]
)


def download(dest: Path) -> Path:
    """Скачать архив, если его ещё нет. Повторный запуск ничего не качает."""
    archive = dest / ARCHIVE
    if archive.exists():
        print(f"архив уже на месте: {archive} ({archive.stat().st_size / 2**20:.0f} МБ)")
        return archive
    dest.mkdir(parents=True, exist_ok=True)
    print(f"качаю {URL}")
    tmp = archive.with_suffix(".part")
    with urllib.request.urlopen(URL) as resp, tmp.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    tmp.rename(archive)
    print(f"скачано: {archive} ({archive.stat().st_size / 2**20:.0f} МБ)")
    return archive


def unpack(archive: Path, dest: Path) -> Path:
    """Распаковать архив. Повторный запуск ничего не распаковывает."""
    root = dest / INNER
    if root.exists():
        print(f"распакованное дерево уже на месте: {root}")
        return root
    print(f"распаковываю {archive}")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)
    return root


def to_int(value: object) -> int:
    """helpful_votes в источнике бывает пустой строкой и строкой с запятыми."""
    if isinstance(value, int):
        return value
    text = str(value or "").replace(",", "").strip()
    return int(text) if text.isdigit() else 0


def to_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def read_product(path: Path) -> dict | None:
    """Один JSON источника → одна строка шарда. Поля переносятся как есть.

    Единственное решение, которое здесь принимается, — какое из нескольких
    резюме взять: у 1291 товара их больше одного. Берём самое подробное
    (pros + cons), при равенстве — первое по порядку в файле. Это выбор
    представителя, а не переработка содержимого.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"  пропускаю {path.name}: {exc}", file=sys.stderr)
        return None

    summaries = payload.get("website_summaries") or []
    if not summaries:
        return None
    best = max(
        summaries,
        key=lambda s: (len(s.get("pros") or []) + len(s.get("cons") or []),),
    )

    meta = payload.get("product_meta") or {}
    reviews = [
        {
            "title": str(r.get("title") or ""),
            "text": str(r.get("text") or ""),
            "rating": to_float(r.get("rating")),
            "helpful_votes": to_int(r.get("helpful_votes")),
            "verified": bool(r.get("verified")),
        }
        for r in (payload.get("customer_reviews") or [])
    ]

    return {
        "asin": path.stem,
        "title": str(meta.get("title") or ""),
        "categories": [str(c) for c in (meta.get("categories") or [])],
        "product_rating": to_float(meta.get("rating")),
        "verdict": str(best.get("verdict") or ""),
        "pros": [str(p) for p in (best.get("pros") or [])],
        "cons": [str(c) for c in (best.get("cons") or [])],
        "summary_source": str(best.get("source") or ""),
        "reviews": reviews,
    }


def shard_order(path: Path) -> str:
    """Ключ раскладки товара по шардам: sha1 от ASIN.

    Имя файла — это ASIN, а ASIN у книг — это ISBN, то есть одни цифры.
    Простой `sorted()` ставит все книги в начало, и первый шард получается
    не выборкой из источника, а полкой «Books»: v1 и v2 оказались бы двумя
    разными предметными областями, а не объёмами одного датасета.
    Хеш имени перемешивает товары детерминированно — состав шарда одинаков
    на любой машине, но каждый шард повторяет состав корпуса.
    """
    return hashlib.sha1(path.stem.encode()).hexdigest()


def write_shards(root: Path, out_dir: Path) -> list[Path]:
    """Разложить продукты по шардам фиксированного размера."""
    files = sorted((root / SUBSET).glob("*.json"), key=shard_order)
    if not files:
        raise SystemExit(f"в {root / SUBSET} нет JSON-файлов — распаковка не удалась")
    print(f"товаров в источнике: {len(files)}")

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.parquet"):
        stale.unlink()

    written: list[Path] = []
    buffer: list[dict] = []
    skipped = 0

    def flush() -> None:
        if not buffer:
            return
        path = out_dir / f"{len(written):04d}.parquet"
        pq.write_table(pa.Table.from_pylist(buffer, schema=SCHEMA), path)
        written.append(path)
        print(f"  {path.name}: {len(buffer)} товаров, {path.stat().st_size / 2**20:.1f} МБ")
        buffer.clear()

    for path in files:
        row = read_product(path)
        if row is None:
            skipped += 1
            continue
        buffer.append(row)
        if len(buffer) >= SHARD_ROWS:
            flush()
    flush()

    print(f"шардов: {len(written)}, пропущено товаров без резюме: {skipped}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        default="../../datasets/amasum",
        help="куда класть источник (по умолчанию рядом с репозиторием, вне git)",
    )
    parser.add_argument(
        "--keep-archive",
        action="store_true",
        help="не удалять zip и распакованное дерево после нарезки шардов",
    )
    args = parser.parse_args()

    dest = (Path(__file__).resolve().parents[1] / args.dest).resolve()
    print(f"источник будет лежать в {dest}")

    archive = download(dest)
    root = unpack(archive, dest)
    shards = write_shards(root, dest / "shards")

    print("\nготово. Пропишите шарды в params.yaml → collect.sources, например:")
    for i, shard in enumerate(shards[:2]):
        print(f"  {shard.relative_to(dest.parents[1])}  (версия v{i + 1})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
