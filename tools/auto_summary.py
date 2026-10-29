#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对位结果统计表（兼容旧 scripts/auto_summary.py 格式）。

表格列：测试 | 车头偏左 | 高度差-左 | 高度差-右 | 停车距离-左 | 停车距离-右 | 时间 |
        初始航向偏差 | 初始横向偏差
  - 人工测量取自 <root>/stats/stat-<YYYYMMDD>.md 各 `## <时间戳>` 小节
  - 初始偏差取自 log 首条 `STARTUP strategy active` 的 hdg / d_head；
    log 缺失时从 bag 感知按控制器同公式复算（--no-bag-fallback 关闭）
  - 统计小结：分布 + 分项达成率 + 全部指标达成率（车头偏左≤lat 且 高度差两侧≤height
    且 停车距离两侧∈[dist_min,dist_max] 且 时间≤time_max 同时满足）

用法:
    python3 <tools>/auto_summary.py
    python3 <tools>/auto_summary.py --date 20261012
    python3 <tools>/auto_summary.py --from 170000 --to 194313 --exclude 190233
    python3 <tools>/auto_summary.py --out <root>/summary/auto_summary-20261012.md
    python3 <tools>/auto_summary.py --dry-run --detail
    python3 <tools>/auto_summary.py --flip-lateral-error
    python3 <tools>/auto_summary.py --thresholds lat=10,height=5,dist_min=120,dist_max=180,time_max=120
"""
import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import test_summary as TS


def initial_deviation(t, bag_fallback=True):
    """返回 (hdg, d_head, src)；src ∈ {log, bag} 或 None。"""
    if t.motion_log:
        for kind, wall, e in C.log_events(t.motion_log):
            if kind == "startup_active":
                return e["hdg"], e["d_head"], "log"
    if bag_fallback and t.bag:
        rows = C.initial_deviations(t)
        if rows:
            w = rows[:5]
            return (st.mean(r["hdg"] for r in w),
                    st.mean(r["d_head"] for r in w), "bag")
    return None


def select_tests(args):
    tests = C.discover_tests()
    excl = [s.strip() for s in args.exclude.split(",") if s.strip()]
    sel = []
    for ts, t in tests.items():
        if args.date and t.date != args.date:
            continue
        if args.from_ts and t.clock < args.from_ts:
            continue
        if args.to_ts and t.clock > args.to_ts:
            continue
        if any(e in ts for e in excl):
            continue
        sel.append(t)
    return sel


def build_rows(sel, flip_lat=False, bag_fallback=True):
    """rows: [id, head, lift_l, lift_r, dist_l, dist_r, (time), hdg, d_head]。
    只保留人工测量完整的测试；returns (rows, times_used, excluded, fb_used)。"""
    has_time = any((C.manual_stats(t) or {}).get("time") is not None for t in sel)
    rows, excl, fb_used = [], [], []
    for t in sel:
        m = C.manual_stats(t) or {}
        if not all(k in m for k in ("head", "lift_l", "lift_r", "dist_l", "dist_r")):
            reason = m.get("marker") or "无人工测量"
            excl.append(f"{t.ts}（{reason}）")
            continue
        head = -m["head"] if flip_lat else m["head"]
        ini = initial_deviation(t, bag_fallback=bag_fallback)
        if ini is None:
            hdg = dh = "-"
        else:
            hdg = f"{ini[0]:+.2f}°"
            dh = f"{ini[1]:+.3f}"
            if ini[2] == "bag":
                fb_used.append(t.ts)
        row = [t.ts, str(head), str(m["lift_l"]), str(m["lift_r"]),
               str(m["dist_l"]), str(m["dist_r"])]
        if has_time:
            row.append(f"{m['time']:g}" if m.get("time") is not None else "-")
        row += [hdg, dh]
        rows.append(row)
    return rows, has_time, excl, fb_used


def build_section(args, rows, has_time, excl, fb_used):
    header = ["测试", "车头偏左", "高度差-左", "高度差-右",
              "停车距离-左", "停车距离-右", "初始航向偏差", "初始横向偏差"]
    if has_time:
        header.insert(6, "时间")
    lines = ["## 对位结果统计", ""]
    if args.note:
        lines += [args.note, ""]
    mark = ""
    no_ini = [r[0] for r in rows if r[-2] == "-"]
    if no_ini:
        mark = f"；{'、'.join(no_ini)} 无法获取初始偏差，标记为 -"
    src = ("初始航向/横向偏差取自各试验 log 首条 STARTUP 周期打印的 hdg 与 d_head"
           "（正值=右偏）" + mark + "。")
    if fb_used:
        src += (f"其中 {'、'.join(fb_used)} 无 STARTUP 日志数据，"
                "由 bag 感知数据（heading_source=1）按控制器相同公式复算补充。")
    lines += [
        f"表格单位 mm，目标停车距离设定 {args.target}mm。",
        src, "",
        "未列入表格的试验（无有效人工测量数据）："
        + ("、".join(excl) if excl else "无") + "。", "",
        C.align_table(header, rows), "",
    ]
    lines += TS.compute_stats(rows, TS.parse_thresholds(args.thresholds),
                              detail=args.detail)
    return "\n".join(lines).rstrip() + "\n"


def update_section(path, section, name="对位结果统计"):
    """替换文件中 `## {name}` 至下一个 `## ` 前的段落；不存在则追加。"""
    p = Path(path)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(section, encoding="utf-8")
        return False
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == f"## {name}"), None)
    if start is None:
        p.write_text(text.rstrip() + "\n\n" + section, encoding="utf-8")
        return False
    end = start + 1
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    new = "\n".join(lines[:start]) + "\n" + section + "\n".join(lines[end:])
    p.write_text(new, encoding="utf-8")
    return True


def main():
    ap = argparse.ArgumentParser(description="对位结果统计表")
    ap.add_argument("--date", default=None, help="只统计某天 YYYYMMDD")
    ap.add_argument("--from", dest="from_ts", default=None, help="起始时间戳（HHMMSS 或 YYYYMMDD_HHMMSS）")
    ap.add_argument("--to", dest="to_ts", default=None, help="结束时间戳")
    ap.add_argument("--exclude", default="", help="逗号分隔排除的时间戳子串")
    ap.add_argument("--note", default="", help="表格上方说明段落")
    ap.add_argument("--target", type=int, default=150, help="目标停车距离 mm（仅说明文字）")
    ap.add_argument("--thresholds",
                    default="lat=10,height=5,dist_min=120,dist_max=180,time_max=120",
                    help="指标阈值")
    ap.add_argument("--flip-lateral-error", action="store_true",
                    help="车头偏左数据正负号反转")
    ap.add_argument("--no-bag-fallback", action="store_true",
                    help="log 无 STARTUP 时不读 bag 复算初始偏差")
    ap.add_argument("--detail", action="store_true", help="附未达标明细")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写文件")
    ap.add_argument("--out", default=None,
                    help="输出文件（默认 <root>/reports/auto_summary.md；已存在时替换其中 "
                         "`## 对位结果统计` 段；- 为打印到终端）")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="只打印不写文件（等价 --out -/--dry-run）")
    ap.add_argument("--root", default=None,
                    help="数据根目录（如 data_1013；默认环境变量 DATA_ROOT 或自动探测）")

    args = ap.parse_args()

    C.init(args.root)

    if args.from_ts and "_" in args.from_ts:
        args.from_ts = args.from_ts.split("_", 1)[1]
    if args.to_ts and "_" in args.to_ts:
        args.to_ts = args.to_ts.split("_", 1)[1]

    sel = select_tests(args)
    if not sel:
        sys.exit("无匹配测试")
    rows, has_time, excl, fb_used = build_rows(
        sel, flip_lat=args.flip_lateral_error,
        bag_fallback=not args.no_bag_fallback)
    if not rows:
        sys.exit("没有可汇总的试验（人工测量均未完成；见下列排除项）\n" + "\n".join(excl))

    section = build_section(args, rows, has_time, excl, fb_used)
    out = C.output_path(args.out, C.ROOT / "reports" / "auto_summary.md",
                        print_only=args.print_only or args.dry_run)
    if out:
        replaced = update_section(out, section)
        print(f"已{'替换' if replaced else '写入'} {out}（n={len(rows)}）")
    else:
        print(section)
        lat, hgt, dmin, dmax, tmax = TS.parse_thresholds(args.thresholds)
        lat_ok = sum(1 for r in rows if abs(int(r[1])) <= lat)
        hgt_ok = sum(1 for r in rows
                      if abs(int(r[2])) <= hgt and abs(int(r[3])) <= hgt)
        dist_ok = sum(1 for r in rows
                      if dmin <= int(r[4]) <= dmax and dmin <= int(r[5]) <= dmax)
        all_ok = sum(1 for r in rows if all([
            abs(int(r[1])) <= lat,
            abs(int(r[2])) <= hgt and abs(int(r[3])) <= hgt,
            dmin <= int(r[4]) <= dmax and dmin <= int(r[5]) <= dmax,
            (not has_time) or (r[6] != "-" and float(r[6]) <= tmax)]))
        print(f"\n# 达成率: lat={lat_ok} height={hgt_ok} dist={dist_ok} "
              f"all={all_ok} (n={len(rows)}，测试 {len(sel)} 个)")


if __name__ == "__main__":
    main()
