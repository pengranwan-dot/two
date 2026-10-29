#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查看 bag 话题 / 消息数 / 时长（无需 rosbag2，直读 sqlite3）。

用法:
    python3 <tools>/bag_info.py 154647
    python3 <tools>/bag_info.py --all
    python3 <tools>/bag_info.py /path/to/bag_dir
"""
import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C


def print_bag(name, bag):
    t0, t1, n = C.bag_duration(bag)
    print(f"== {name}  messages={n}  duration={t1 - t0:.1f}s" if t0 else f"== {name}  空")
    if t0:
        print(f"   {datetime.datetime.fromtimestamp(t0)} -> {datetime.datetime.fromtimestamp(t1)}")
    for topic, typ, cnt in C.bag_topics(bag):
        print(f"   {cnt:8d}  {topic:45s} {typ}")


def main():
    ap = argparse.ArgumentParser(description="bag 话题信息")
    ap.add_argument("test", nargs="?", default=None, help="测试时间戳（如 154647）或 bag 目录")
    ap.add_argument("--all", action="store_true", help="列出全部测试的一句话摘要")
    ap.add_argument("--root", default=None,
                    help="数据根目录（如 data_1013；默认环境变量 DATA_ROOT 或自动探测）")

    args = ap.parse_args()

    if args.test and not args.all and Path(args.test).is_dir():
        print_bag(Path(args.test).name, Path(args.test))
        return

    C.init(args.root)

    tests = C.discover_tests()
    if args.all:
        for ts, t in tests.items():
            if not t.bag:
                continue
            t0, t1, n = C.bag_duration(t.bag)
            dur = f"{t1 - t0:.0f}s" if t0 else "--"
            print(f"{ts}  messages={n:7d}  duration={dur}")
        return
    if not args.test:
        ap.error("需要测试时间戳或 --all")
    print_bag(args.test, C.resolve_test(args.test, tests).bag)


if __name__ == "__main__":
    main()
