"""Стадия split: разбиение на train/val/test."""

import json
import random
import time
from pathlib import Path

from src.config import load_params
from src.contamination import report
from src.schema import Example, dump, iter_examples
from src.textnorm import normalize_group


def group_split(sizes: dict[str, int], ratios: dict[str, float], seed: int) -> dict[str, str]:
    """Раздать метки сплита ЦЕЛЫМ группам, а не отдельным строкам.

    Единица разбиения обязана совпадать с единицей зависимости между строками.
    Здесь строки одной группы — товары одной полки: у них общий словарь,
    общие формулировки плюсов и минусов и похожий набор отзывов. Разложив
    такие строки по обе стороны границы, мы показываем модели на обучении
    почти тот же пример, по которому потом её меряем, — и тест начинает
    завышать качество, не сломав ни схему, ни дедупликацию.

    Раздача жадная: группы идут от крупных к мелким (крупной труднее найти
    место, и оставлять её напоследок — значит гарантированно промахнуться
    по долям), каждая уходит в сплит с наибольшим недобором. Порядок равных
    по размеру групп задаёт seed, а не алфавит: иначе сплит определялся бы
    тем, как называется товарная полка.
    """
    total = sum(sizes.values())
    rng = random.Random(seed)
    jitter = {g: rng.random() for g in sorted(sizes)}
    order = sorted(sizes, key=lambda g: (-sizes[g], jitter[g], g))

    targets = {name: total * share for name, share in ratios.items()}
    filled = {name: 0 for name in ratios}
    labels: dict[str, str] = {}
    for group in order:
        name = max(ratios, key=lambda n: (targets[n] - filled[n], n))
        labels[group] = name
        filled[name] += sizes[group]
    return labels


def main() -> None:
    params = load_params()
    paths = params["paths"]
    cfg = params["split"]
    started = time.perf_counter()

    examples: list[Example] = list(iter_examples(paths["clean"]))
    if cfg["group_key"] != "topic":
        raise SystemExit(f"неизвестный split.group_key: {cfg['group_key']!r}")

    sizes: dict[str, int] = {}
    for ex in examples:
        key = normalize_group(ex.topic)
        sizes[key] = sizes.get(key, 0) + 1

    labels = group_split(sizes, cfg["ratios"], cfg["seed"])
    buckets: dict[str, list[Example]] = {name: [] for name in cfg["ratios"]}
    for ex in examples:
        buckets[labels[normalize_group(ex.topic)]].append(ex)

    for name, rows in buckets.items():
        out = Path(paths[name])
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for ex in rows:
                fh.write(dump(ex) + "\n")

    nd = params["clean"]["near_dup"]
    rep = report(
        buckets["train"],
        buckets["test"],
        shingle_words=nd["shingle_words"],
        num_perm=nd["num_perm"],
        threshold=params["contamination"]["threshold"],
    )

    metrics = {
        "version": params["collect"]["version"],
        "seed": cfg["seed"],
        "group_key": cfg["group_key"],
        "groups_total": len(sizes),
        "sizes": {name: len(rows) for name, rows in buckets.items()},
        "groups": {
            name: len({normalize_group(ex.topic) for ex in rows}) for name, rows in buckets.items()
        },
        "ratios_actual": {
            name: round(len(rows) / len(examples), 4) for name, rows in buckets.items()
        },
        "contamination": rep,
        "seconds": round(time.perf_counter() - started, 2),
    }
    mpath = Path(paths["metrics_split"])
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        "split: "
        + ", ".join(f"{name} {len(rows)}" for name, rows in buckets.items())
        + f" (групп {len(sizes)}, {metrics['seconds']} с)"
    )


if __name__ == "__main__":
    main()
