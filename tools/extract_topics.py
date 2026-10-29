#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 bag 中的数值话题导出为 CSV 缓存（<root>/csv/<ts>/<topic>.csv）。

供后续分析（phase2_stats / recalc_initial / 自写脚本）快速读取，避免反复解 bag。
列格式：time,<字段...>；time 为 bag 记录 epoch 秒。

用法:
    python3 <tools>/extract_topics.py 154647                 # 默认话题集
    python3 <tools>/extract_topics.py 154647 --list          # 只列出可导出话题
    python3 <tools>/extract_topics.py 154647 --topics /arc_detector/height,/arc_detector/dist_x_l
    python3 <tools>/extract_topics.py --all --date 20261012
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

DEFAULT_TOPICS = [
    "/arc_detector/target_point",
    "/arc_detector/ground_plane",
    "/arc_detector/arch_normal",
    "/arc_detector/top_line_dir",
    "/arc_detector/phase_state",
    "/arc_detector/height",
    "/arc_detector/height_l",
    "/arc_detector/height_r",
    "/arc_detector/dist_x_l",
    "/arc_detector/dist_x_r",
    "/arc_detector/distance_diff",
    "/arc_detector/ground_height_l_2d",
    "/arc_detector/ground_height_r_2d",
    "/arc_detector/rect_center_y",
    "/chcnav/devpvt",
    "/temperature",
    "/temperature_env",
    "/humidity_env",
]


def export(t, topics):
    have = {name: typ for name, typ, n in C.bag_topics(t.bag) if n}
    done, skipped = [], []
    for topic in topics:
        if topic not in have or have[topic] not in C.DECODERS:
            skipped.append(topic)
            continue
        cols, dec = C.DECODERS[have[topic]]
        out = C.CSV / t.ts / (topic.lstrip("/") + ".csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            header = ["time"] + ([cols] if isinstance(cols, str) else list(cols))
            w.writerow(header)
            for ts, data in C.bag_series(t.bag, topic):
                try:
                    v = dec(data)
                except Exception:
                    continue
                vals = [v] if isinstance(cols, str) else list(v)
                w.writerow([f"{ts:.9f}"] + [f"{x:.9g}" for x in vals])
                n += 1
        done.append((str(out.relative_to(C.ROOT)), n))
    return done, skipped


def main():
    ap = argparse.ArgumentParser(description="bag 数值话题 → CSV 缓存")
    ap.add_argument("test", nargs="?", help="测试时间戳（如 154647）")
    ap.add_argument("--all", action="store_true", help="导出全部测试")
    ap.add_argument("--date", default=None, help="配合 --all：只处理某天 YYYYMMDD")
    ap.add_argument("--topics", default=None, help="逗号分隔话题；默认常用话题集")
    ap.add_argument("--list", action="store_true", help="只列出 bag 中可导出的话题")
    ap.add_argument("--force", action="store_true", help="已存在 CSV 也重新导出")
    ap.add_argument("--root", default=None,
                    help="数据根目录（如 data_1013；默认环境变量 DATA_ROOT 或自动探测）")

    args = ap.parse_args()

    C.init(args.root)

    tests = C.discover_tests()
    if args.all:
        sel = [t for t in tests.values() if t.bag and (not args.date or t.date == args.date)]
    elif args.test:
        sel = [C.resolve_test(args.test, tests)]
    else:
        ap.error("需要测试时间戳或 --all")

    topics = [s.strip() for s in args.topics.split(",") if s.strip()] if args.topics else DEFAULT_TOPICS
    for t in sel:
        if not t.bag:
            print(f"[跳过] {t.ts}: 无 bag")
            continue
        if args.list:
            print(f"== {t.ts}")
            for name, typ, n in C.bag_topics(t.bag):
                mark = "可导出" if typ in C.DECODERS and n else "跳过"
                print(f"   {mark:6s} {n:8d}  {name:45s} {typ}")
            continue
        if not args.force:
            todo = [x for x in topics
                    if not (C.CSV / t.ts / (x.lstrip("/") + ".csv")).exists()]
        else:
            todo = topics
        done, skipped = export(t, todo)
        for path, n in done:
            print(f"{t.ts}  {path}  ({n} 行)")
        for x in skipped:
            print(f"{t.ts}  [跳过] {x}（无话题或无解码器）")
    print("完成")


if __name__ == "__main__":
    main()
