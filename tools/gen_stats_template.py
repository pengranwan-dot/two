#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按数据目录中已有测试的时间戳生成人工测量模板 <root>/stats/stat-<YYYYMMDD>.md。

数据来源：bags/autorecord_<ts>、logs/*_<ts>.log（即 common.discover_tests()），
日期取测试时间戳前 8 位；一个目录含多天测试时按天分别生成文件。
模板中五项字段只写字段名，数值留空由人工填写（格式见 README 1.3）：

    ## 20261016_115052

    - 车头偏左
    - lift
    - 车头左右距离
    - 时间 100

未填数值的字段不会被 parse_stats_file 识别，因此未测量的测试自动视为“未测量”，
不会污染统计结果。默认是**增量**模式：已存在的 stat 文件只追加缺失小节，
已填写的内容（含“有问题”等说明）原样保留；`--force` 才整份重建（旧文件备份为 .bak）。

用法:
    python3 tools/gen_stats_template.py --root data-1016
    python3 tools/gen_stats_template.py --root data-1016 --print
    python3 tools/gen_stats_template.py --root data-1016 --time - --out /tmp/stat.md
    python3 tools/gen_stats_template.py --root data-1016 --force
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

HINT = (
    "> 人工测量：在字段名后填实测值（车头偏左 / lift 左 右 / 车头左右距离 左 右，单位 mm；"
    "时间单位 s）。\n"
    "> 五项齐全才会进入对位统计与达成率；整节只写“有问题”表示异常（不参与统计）。\n"
)


def section(ts, time_text):
    lines = ["## " + ts, "", "- 车头偏左", "- lift", "- 车头左右距离"]
    lines.append(("- 时间 " + time_text).rstrip())
    lines.append("")
    return "\n".join(lines)


def existing_sections(path):
    """返回 (原文, 已存在的时间戳列表)。"""
    if not path.exists():
        return "", []
    text = path.read_text(encoding="utf-8", errors="replace")
    return text, re.findall(r"^##\s+(\d{8}_\d{6})\s*$", text, flags=re.M)


def main():
    ap = argparse.ArgumentParser(
        description="按数据目录中的测试时间戳生成人工测量模板 stat-<YYYYMMDD>.md")
    ap.add_argument("--root", default=None, help="数据根目录（如 data-1016；默认自动探测）")
    ap.add_argument("--date", default=None, help="只生成某天 YYYYMMDD（默认按测试自动分组）")
    ap.add_argument("--from", dest="from_ts", default=None, help="起始时间戳（HHMMSS）")
    ap.add_argument("--to", dest="to_ts", default=None, help="结束时间戳（HHMMSS）")
    ap.add_argument("--exclude", default="", help="逗号分隔排除的时间戳子串")
    ap.add_argument("--time", default="100",
                    help="时间字段的预填值（默认 100；给 - 则时间也留空）")
    ap.add_argument("--all", action="store_true",
                    help="包含只有 log、没有 bag 的测试（默认要求 bag+motion log 齐全）")
    ap.add_argument("--force", action="store_true",
                    help="整份重建（覆盖已填写内容，旧文件备份为 .bak）")
    ap.add_argument("--out", default=None,
                    help="输出路径（仅单天时可用；默认 <root>/stats/stat-<date>.md）")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="只打印不写文件")
    args = ap.parse_args()

    C.init(args.root, quiet=bool(args.root))
    tests = C.discover_tests()
    if not tests:
        sys.exit(f"{C.ROOT} 下未发现测试（bags/ 或 logs/ 为空？）")

    excl = [s.strip() for s in args.exclude.split(",") if s.strip()]
    groups = {}
    for ts, t in tests.items():
        if args.date and t.date != args.date:
            continue
        if args.from_ts and t.clock < args.from_ts:
            continue
        if args.to_ts and t.clock > args.to_ts:
            continue
        if any(e in ts for e in excl):
            continue
        if not args.all and not t.files_ok():
            continue
        groups.setdefault(t.date, []).append(ts)
    if not groups:
        sys.exit("无匹配测试")

    if args.out and len(groups) > 1:
        sys.exit("测试跨多天，--out 只能用于单天；请用 --date 指定某天")
    time_text = "" if args.time in ("-", "?", "") else args.time
    total_new = total_old = 0

    for date in sorted(groups):
        tss = sorted(groups[date])
        default = C.STATS / f"stat-{date}.md"
        if args.out:
            out = Path(args.out)
        elif args.print_only:
            out = None
        else:
            out = C.output_path(None, default)

        old_text, old_ts = ("", []) if not out else existing_sections(out)
        if args.force:
            old_text, old_ts = "", []
            if out and out.exists() and out.read_text(encoding="utf-8", errors="replace").strip():
                bak = out.with_suffix(out.suffix + ".bak")
                bak.write_text(out.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
                print(f"[info] 已备份旧文件 → {bak}")

        missing = [ts for ts in tss if ts not in old_ts]
        total_new += len(missing)
        total_old += len(tss) - len(missing)

        if old_text.strip():
            text = old_text
            if missing:
                if not text.endswith("\n"):
                    text += "\n"
                if not text.endswith("\n\n"):
                    text += "\n"
                text += "\n".join(section(ts, time_text) for ts in missing)
        else:
            text = (f"# 人工测量统计 {date}\n\n{HINT}\n"
                    + "\n".join(section(ts, time_text) for ts in tss))

        if out:
            C.write_text(out, text)
        else:
            print(text)

    if not args.print_only or len(groups) > 1:
        print(f"[info] 测试 {total_new + total_old} 个：新增 {total_new}，已存在 {total_old}"
              f"（保留原内容；--force 可重建）", file=sys.stderr)


if __name__ == "__main__":
    main()
