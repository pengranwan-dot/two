#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出 <root> 下的测试清单及数据完整性。

用法:
    python3 <tools>/list_tests.py
    python3 <tools>/list_tests.py --date 20261012
    python3 <tools>/list_tests.py --detail 154647
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C


def main():
    ap = argparse.ArgumentParser(description="测试清单")
    ap.add_argument("--date", default=None, help="只列出某天 YYYYMMDD")
    ap.add_argument("--detail", default=None, help="打印单个测试的原始路径")
    ap.add_argument("--root", default=None,
                    help="数据根目录（如 data_1013；默认环境变量 DATA_ROOT 或自动探测）")

    args = ap.parse_args()

    C.init(args.root)

    tests = C.discover_tests()
    if args.detail:
        t = C.resolve_test(args.detail, tests)
        print(f"测试 {t.ts}")
        for k in ("bag", "motion_log", "arc_log", "recorder_log", "config", "note"):
            print(f"  {k:12s}: {getattr(t, k)}")
        return

    header = ["测试", "bag", "mb", "时长(s)", "motion", "arc", "rec", "config快照",
              "note", "人工测量"]
    rows = []
    for ts, t in tests.items():
        if args.date and t.date != args.date:
            continue
        mb = dur = "--"
        if t.bag:
            try:
                mb = f"{sum(p.stat().st_size for p in t.bag.glob('*.db3')) / 1e6:.0f}"
                t0, t1, _ = C.bag_duration(t.bag)
                dur = f"{t1 - t0:.0f}" if t0 else "--"
            except OSError:
                pass
        m = C.manual_stats(t)
        if m and "head" in m:
            stats = "有"
        elif m and m.get("marker"):
            stats = m["marker"]
        else:
            stats = "--"
        rows.append([
            ts, "有" if t.bag else "--", mb, dur,
            "有" if t.motion_log else "--",
            "有" if t.arc_log else "--",
            "有" if t.recorder_log else "--",
            t.config.name.replace("autonomous_config-", "") if t.config else "--",
            "有" if t.note else "--", stats])
    print(C.align_table(header, rows))
    print(f"\n共 {len(rows)} 个测试（{C.BAGS}）")


if __name__ == "__main__":
    main()
