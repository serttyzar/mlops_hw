"""Стадия collect: источник → data/raw.jsonl.

Источник — AmaSum: на каждый товар Amazon есть 10–100 отзывов покупателей и
резюме, написанное редактором обзорного сайта (verdict / pros / cons).
Сырьё разложено по parquet-шардам скриптом scripts/fetch_source.py.

Контракт стадии, а не её внутренности, держит остальной пайплайн:
на выходе JSONL со строками {"id", "topic", "messages": [system, user, assistant]}.

Скачанный чужой набор сам по себе сдачей не является (README, «Готовый датасет
как источник»). Поэтому стадия не перекладывает parquet в JSONL один в один,
а делает пять вещей, и каждая видна числом в metrics/collect.json:

  1. чинит таксономию источника: пути категорий в AmaSum записаны то от корня
     к листу («Books > Sports & Outdoors > Mountaineering»), то наоборот
     («Earbud Headphones > Electronics»). Брать categories[0] за отдел —
     значит получить мусорные группы и течь на сплите;
  2. отбирает и обрезает отзывы: из 10–100 остаются reviews_per_example самых
     полезных, с резервом под критические, иначе пример не влезает в лимит длин;
  3. сверяет чужую разметку: пункты pros/cons без лексической опоры в отзывах
     означают, что редактор писал по товару, а не по этим отзывам. Такое резюме
     выбрасывается, а не переносится в обучение;
  4. ограничивает долю группы (collect.max_group_share), чтобы одна товарная
     полка не решала за весь датасет;
  5. разводит инструкцию на варианты (collect.system_prompts), чтобы модель
     не заучила её формулировку.
"""

import hashlib
import json
import math
import re
import time
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

from src.config import load_params, source_files
from src.textnorm import normalize_text

COLUMNS = [
    "asin",
    "title",
    "categories",
    "product_rating",
    "verdict",
    "pros",
    "cons",
    "reviews",
]
BATCH = 500

# Слова, по которым нельзя судить об обоснованности пункта резюме: они есть
# в любом отзыве и сделали бы проверку обоснованности всегда зелёной.
STOPWORDS = frozenset(
    """
    about after also always another anything around because been before being
    beside better between both bought came cannot could didn does doesn doing
    done down during each either enough even ever every everything from
    getting give going good great half have having here however into
    itself just keep kept like little long look looking made make makes many
    more most much must need needs never next nice nothing only other others
    over own perfect place pretty product quite range really right said same
    seem seems since some something still such take taken than that their them
    then there these they thing things think this those though through time
    together took totally under until used using very want wanted well were
    what when where which while will with within without work worked works
    would your yours
    """.split()
)


def pick_prompt(example_id: str, variants: list[str]) -> str:
    """Детерминированно выбрать вариант инструкции по id примера.

    Именно sha1, а не встроенный hash(): тот солится на каждый запуск процесса,
    и raw.jsonl переставал бы быть воспроизводимым.
    """
    digest = hashlib.sha1(example_id.encode("utf-8")).hexdigest()
    return variants[int(digest, 16) % len(variants)]


def content_words(text: str) -> set[str]:
    """Значимые слова текста: без стоп-слов и коротышей.

    Нормализация — тем же normalize_text, что у дедупликации и проверки
    контаминации: три почти одинаковых нормализатора в одном проекте рано или
    поздно начинают мерить разное.
    """
    return {
        w
        for w in re.findall(r"[a-z0-9]+", normalize_text(text))
        if len(w) >= 4 and w not in STOPWORDS
    }


def category_frequencies(rows: list[dict]) -> Counter:
    """Сколько товаров упоминает каждую категорию.

    Это и есть лекарство от кривых путей источника: чем выше категория в
    иерархии Amazon, тем в большем числе товаров она встречается. Частотность
    даёт порядок «общее → частное», не полагаясь на порядок внутри записи.
    """
    df: Counter = Counter()
    for row in rows:
        for cat in {c.strip() for c in (row["categories"] or []) if c and c.strip()}:
            df[cat] += 1
    return df


def group_key(categories: list[str], df: Counter) -> str | None:
    """Ключ группы: «отдел > подраздел», выправленный по частотности.

    Возвращает None, если категорий нет вообще (115 таких товаров на 25 203):
    такую строку не к чему отнести, а в сплит по группам она обязана попасть
    целиком вместе со своими соседями.
    """
    cats = sorted({c.strip() for c in (categories or []) if c and c.strip()})
    if not cats:
        return None
    ordered = sorted(cats, key=lambda c: (-df[c], c))
    return " > ".join(ordered[:2])


def truncate(text: str, limit: int) -> str:
    """Обрезать по границе слова. Резать посреди слова — портить токенизацию в ДЗ 4."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]).rstrip(",.;:-") + "…"


def select_reviews(reviews: list[dict], cfg: dict) -> list[dict]:
    """Отобрать отзывы для примера: самые полезные плюс критические.

    Сортировка только по helpful_votes даёт почти всегда хвалебную подборку:
    полезными отмечают восторженные отзывы. Тогда в ответе минусы есть,
    а в отзывах — нет, и модель учится выдумывать недостатки.
    Поэтому часть слотов резервируется под отзывы с низким рейтингом.
    """
    usable = [r for r in reviews if (r.get("text") or "").strip()]
    if not usable:
        return []

    # Индекс — стабильный тай-брейк: при равных голосах порядок обязан быть
    # тем же на любой машине, иначе raw.jsonl невоспроизводим.
    ranked = sorted(enumerate(usable), key=lambda p: (-p[1]["helpful_votes"], p[0]))
    limit = cfg["reviews_per_example"]
    critical_slots = min(cfg["min_critical_reviews"], limit)

    chosen: list[tuple[int, dict]] = []
    taken: set[int] = set()
    for idx, rev in ranked:
        if len(chosen) >= critical_slots:
            break
        if rev["rating"] and rev["rating"] <= cfg["critical_rating"]:
            chosen.append((idx, rev))
            taken.add(idx)
    for idx, rev in ranked:
        if len(chosen) >= limit:
            break
        if idx not in taken:
            chosen.append((idx, rev))
            taken.add(idx)

    # Возвращаем в исходном порядке источника, а не в порядке отбора:
    # позиция отзыва не должна кодировать, за что его выбрали.
    return [rev for _, rev in sorted(chosen, key=lambda p: p[0])]


def is_grounded(words: set[str], haystack: set[str], min_hits: int, min_overlap: float) -> bool:
    """Опирается ли пункт резюме на текст отзывов.

    Критерия два, и выполнить достаточно один. Чистая доля совпавших слов
    не работает: у половины пунктов источника 3–5 значимых слов, поэтому доля
    грубо квантуется и меряет длину пункта, а не обоснованность. На замерах
    по шарду она пропускала «21-inch tall Superman plushie» (1 совпадение из 4,
    и то по названию товара — доля 0.25) и браковала «Features time-lapse,
    driving, loop recording, and slow-motion shooting modes» (2 содержательных
    совпадения из 9 — доля 0.22). То есть ровно наоборот.

      * min_item_hits  — абсолютный порог: сколько значимых слов пункта нашлось
        в отзывах. Не даёт длинному конкретному пункту проиграть короткому;
      * min_item_overlap — относительный порог, он же единственный работающий
        для пунктов в одно-два слова, где до абсолютного порога не дотянуться.
    """
    hits = len(words & haystack)
    return hits >= min_hits or hits / len(words) >= min_overlap


def grounded_share(items: list[str], reviews_text: str, cfg: dict) -> float:
    """Доля пунктов резюме, опирающихся на текст отзывов.

    Проверка намеренно грубая и лексическая: она ловит не «неточность
    формулировки», а пункты, которых в этих отзывах нет вовсе.
    """
    if not items:
        return 0.0
    haystack = content_words(reviews_text)
    min_hits = cfg["min_item_hits"]
    min_overlap = cfg["min_item_overlap"]
    grounded = 0
    for item in items:
        words = content_words(item)
        if not words:
            continue
        if is_grounded(words, haystack, min_hits, min_overlap):
            grounded += 1
    return grounded / len(items)


def clean_items(values: list[str], min_words: int) -> list[str]:
    """Отбросить обрывки: в источнике длинный пункт бывает разрезан по запятой
    на «simply find the section that pertains to your situation» и хвост из двух слов."""
    out: list[str] = []
    for raw in values or []:
        item = " ".join(str(raw).split()).strip(" .;,")
        if item and len(item.split()) >= min_words:
            out.append(item)
    return out


def build_user(title: str, topic: str, reviews: list[dict], cfg: dict) -> str:
    """Реплика пользователя: товар, его полка и пронумерованные отзывы."""
    lines = [f"Product: {title}", f"Category: {topic}", "", "Customer reviews:"]
    for i, rev in enumerate(reviews, start=1):
        rating = f"{rev['rating']:.0f}/5" if rev["rating"] else "n/a"
        head = " ".join((rev.get("title") or "").split())
        body = truncate(rev["text"], cfg["max_review_chars"])
        lines.append(f"{i}. [{rating}] {head + ' — ' if head else ''}{body}")
    return "\n".join(lines)


def build_assistant(verdict: str, pros: list[str], cons: list[str]) -> str:
    """Реплика ассистента: вердикт, плюсы, минусы.

    Формат один на весь датасет, длина — нет: она следует за числом пунктов,
    а оно у товаров разное. Именно на этом держится гейт разброса длин.
    """
    lines = [f"Verdict: {' '.join(verdict.split())}", "", "Pros:"]
    lines += [f"- {p}" for p in pros]
    lines += ["", "Cons:"]
    lines += [f"- {c}" for c in cons]
    return "\n".join(lines)


def read_rows(files: list[Path]) -> list[list[dict]]:
    """Прочитать шарды. Список списков: n_rows отсекается для КАЖДОГО файла."""
    per_file: list[list[dict]] = []
    for src in files:
        rows: list[dict] = []
        for batch in pq.ParquetFile(src).iter_batches(batch_size=BATCH, columns=COLUMNS):
            rows.extend(batch.to_pylist())
        per_file.append(rows)
    return per_file


def main() -> None:
    params = load_params()
    cfg = params["collect"]
    paths = params["paths"]
    n_rows = cfg["n_rows"]
    variants = cfg["system_prompts"]
    if not variants:
        raise SystemExit("collect.system_prompts пуст: инструкцию брать неоткуда")
    topics = cfg["topics"]
    wanted = set(topics) if topics else None

    out = Path(paths["raw"])
    out.parent.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    files = source_files(params)
    per_file = read_rows(files)

    # Таксономия считается по всем шардам версии сразу: группа товара не должна
    # зависеть от того, в какой шард он попал при нарезке.
    df = category_frequencies([row for rows in per_file for row in rows])

    # Кап считается от заказанного объёма, а не от фактического: иначе он
    # менялся бы по ходу прохода и результат зависел бы от порядка строк.
    group_cap = max(1, math.ceil(n_rows * len(files) * cfg["max_group_share"]))
    group_seen: Counter = Counter()

    scanned = written = 0
    dropped_no_category = dropped_topic = dropped_incomplete = 0
    dropped_no_reviews = dropped_ungrounded = dropped_group_cap = 0
    prompts_used: set[str] = set()
    grounded_kept: list[float] = []

    with out.open("w", encoding="utf-8") as fh:
        for rows in per_file:
            taken = 0
            # Фильтры применяются ДО отсечки n_rows: иначе «первые 3000 строк»
            # и «3000 строк по теме» — разные вещи, и сужение набора давало бы
            # случайный огрызок вместо заказанного объёма.
            for row in rows:
                if taken >= n_rows:
                    break
                scanned += 1

                topic = group_key(row["categories"], df)
                if topic is None:
                    dropped_no_category += 1
                    continue
                if wanted is not None and topic not in wanted:
                    dropped_topic += 1
                    continue

                pros = clean_items(row["pros"], cfg["min_item_words"])
                cons = clean_items(row["cons"], cfg["min_item_words"])
                verdict = " ".join(str(row["verdict"] or "").split())
                if not verdict or not pros or not cons:
                    dropped_incomplete += 1
                    continue
                if len(pros) + len(cons) < cfg["min_summary_items"]:
                    dropped_incomplete += 1
                    continue

                reviews = select_reviews(row["reviews"] or [], cfg)
                if not reviews:
                    dropped_no_reviews += 1
                    continue

                # Обоснованность сверяется ровно с тем текстом, который увидит
                # модель, — с ОБРЕЗАННЫМ. Считать по полному отзыву значит
                # заверять пункт доказательством, которое в пример не попадёт.
                reviews_text = " ".join(
                    f"{r.get('title') or ''} {truncate(r['text'], cfg['max_review_chars'])}"
                    for r in reviews
                )
                share = grounded_share(pros + cons, reviews_text, cfg)
                if share < cfg["min_grounded_share"]:
                    dropped_ungrounded += 1
                    continue

                if group_seen[topic] >= group_cap:
                    dropped_group_cap += 1
                    continue

                prompt = pick_prompt(row["asin"], variants)
                prompts_used.add(prompt)
                record = {
                    "id": row["asin"],
                    "topic": topic,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {
                            "role": "user",
                            "content": build_user(row["title"], topic, reviews, cfg),
                        },
                        {
                            "role": "assistant",
                            "content": build_assistant(verdict, pros, cons),
                        },
                    ],
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                group_seen[topic] += 1
                grounded_kept.append(share)
                taken += 1
                written += 1

    largest_group, largest_n = (group_seen.most_common(1) or [("", 0)])[0]
    metrics = {
        "version": cfg["version"],
        "files": len(files),
        "rows_scanned": scanned,
        "rows_written": written,
        "dropped_no_category": dropped_no_category,
        "dropped_topic_filter": dropped_topic,
        "dropped_incomplete_summary": dropped_incomplete,
        "dropped_no_reviews": dropped_no_reviews,
        "dropped_ungrounded": dropped_ungrounded,
        "dropped_group_cap": dropped_group_cap,
        "topics_filter": len(wanted) if wanted else 0,
        "groups": len(group_seen),
        "group_cap": group_cap,
        "largest_group": largest_group,
        "largest_group_share": round(largest_n / written, 4) if written else 0.0,
        "grounded_share_mean": (
            round(sum(grounded_kept) / len(grounded_kept), 4) if grounded_kept else 0.0
        ),
        "system_prompt_variants": len(prompts_used),
        "seconds": round(time.perf_counter() - started, 2),
    }
    mpath = Path(paths["metrics_collect"])
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"collect: версия {cfg['version']}, файлов {metrics['files']}, "
        f"просмотрено {scanned}, записано {written} "
        f"(без категории -{dropped_no_category}, фильтр тем -{dropped_topic}, "
        f"неполное резюме -{dropped_incomplete}, без отзывов -{dropped_no_reviews}, "
        f"не обосновано отзывами -{dropped_ungrounded}, кап группы -{dropped_group_cap}), "
        f"групп {len(group_seen)} при капе {group_cap}, "
        f"вариантов инструкции {len(prompts_used)}, "
        f"{metrics['seconds']} с → {out}"
    )


if __name__ == "__main__":
    main()
