#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段二 ground height / ltg roll 统计（兼容旧 scripts/ground_height_phase2.py）。

区间：阶段二开始（log `STAGE 1d -> 2`）→ `PHASE2-STOP active`（不含）。
数据源：bag（或 csv 缓存）ground_height_l/r_2d、height_l/r、chcnav/devpvt + 控制器 log。

列：roll_target / chcnav 窗口均值 / log ltg 窗口均值 / ltg 区间估算 /
    ltg 停稳估算（STOP 后末尾 50 点）/ phase2 height target / gh_l,r 均值 /
    height_l,r 均值 / 末 hlr / 末 nav / 末 dist_x L,R。

用法:
    python3 <tools>/phase2_stats.py                  # 全部有效测试
    python3 <tools>/phase2_stats.py --date 20261012
    python3 <tools>/phase2_stats.py --from 170000 --all
    python3 <tools>/phase2_stats.py --out <root>/phase2_stats.md
"""
import argparse
import math
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import metrics as M

LATERAL_SPAN = 1.5
STOP_TAIL_N = 50
TOPICS = {
    "gh_l": "/arc_detector/ground_height_l_2d",
    "gh_r": "/arc_detector/ground_height_r_2d",
    "h_l": "/arc_detector/height_l",
    "h_r": "/arc_detector/height_r",
    "pvt": "/chcnav/devpvt",
}


def ltg_stop_roll(gl, gr, hl, hr, t1):
    """STOP 之后末尾 N 点 (gh-h) 差的 roll 估算。"""
    if not (gl and gr):
        return None, 0
    fgr, fhl, fhr = C.interp_series(gr), C.interp_series(hl), C.interp_series(hr)
    pairs = []
    for t, vgl in gl:
        if t < t1:
            continue
        pairs.append((vgl - fhl(t)) - (fgr(t) - fhr(t)))
    if not pairs:
        return None, 0
    tail = pairs[-STOP_TAIL_N:]
    return math.degrees(math.atan2(st.mean(tail), LATERAL_SPAN)), len(tail)


def main():
    ap = argparse.ArgumentParser(description="阶段二 ground height / ltg 统计")
    ap.add_argument("--date", default=None, help="只统计某天 YYYYMMDD")
    ap.add_argument("--from", dest="from_ts", default=None, help="起始时间戳 HHMMSS")
    ap.add_argument("--to", dest="to_ts", default=None, help="结束时间戳 HHMMSS")
    ap.add_argument("--all", action="store_true", help="缺数据/未进阶段二的测试也列出")
    ap.add_argument("--out", default=None,
                    help="输出 markdown（默认 <root>/reports/phase2_stats.md；- 为打印到终端）")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="只打印不写文件（等价 --out -）")
    ap.add_argument("--root", default=None,
                    help="数据根目录（如 data_1013；默认环境变量 DATA_ROOT 或自动探测）")

    args = ap.parse_args()

    C.init(args.root)

    tests = C.discover_tests()
    sel = []
    for ts, t in tests.items():
        if args.date and t.date != args.date:
            continue
        if args.from_ts and t.clock < args.from_ts:
            continue
        if args.to_ts and t.clock > args.to_ts:
            continue
        sel.append(t)

    header = ["测试", "roll_tgt(°)", "chcnav窗口(°)", "log_ltg窗口(°)",
              "ltg区间估算(°)", "ltg停稳估算(°)", "h target(m)",
              "gh_l(m)", "gh_r(m)", "height_l(m)", "height_r(m)",
              "末hlr(°)", "末nav(°)", "末dist_x L/R(m)",
              "高度差-左(mm)", "高度差-右(mm)", "人工roll(°)"]
    rows, skipped = [], []
    for t in sel:
        d = M.summarize(t, bag_fallback=False)
        last = d.get("p2_last") or {}
        ev = d.get("events") or {}
        p2 = ev.get("stage_1d_2")
        stop = ev.get("phase2_stop_active")
        roll_tgt = d.get("roll_tgt")
        ht = d.get("height_target")
        m = C.manual_stats(t) or {}
        man = [C.fmt(m.get("lift_l")), C.fmt(m.get("lift_r"))]
        roll_man = C.manual_roll_deg(m.get("lift_l"), m.get("lift_r"))
        man.append(f"{roll_man:+.3f}" if roll_man is not None else "--")
        if not p2 or not stop or not t.bag:
            skipped.append(t.ts)
            if args.all:
                rows.append([t.ts, f"{roll_tgt:+.2f}" if roll_tgt is not None else "--",
                             *["--"] * 12] + man)
            continue
        t0 = C.wall_to_epoch(t, p2[0])
        t1 = C.wall_to_epoch(t, stop[0])
        series = {k: C.load_series(t, topic) for k, topic in TOPICS.items()}
        gl, gr, hl, hr = series["gh_l"], series["gh_r"], series["h_l"], series["h_r"]
        if t0 is None or t1 is None or not (gl and gr and hl and hr):
            skipped.append(t.ts)
            if args.all:
                rows.append([t.ts, f"{roll_tgt:+.2f}" if roll_tgt is not None else "--",
                             *["--"] * 12])
            continue
        (mgl, n1), (mgr, _), (mhl, _), (mhr, _) = (
            C.mean_window(gl, t0, t1), C.mean_window(gr, t0, t1),
            C.mean_window(hl, t0, t1), C.mean_window(hr, t0, t1))
        ltg_interval = math.degrees(math.atan2((mgl - mhl) - (mgr - mhr), LATERAL_SPAN)) \
            if None not in (mgl, mgr, mhl, mhr) else None
        ltg_stop, nstop = ltg_stop_roll(gl, gr, hl, hr, t1)
        chc, _ = C.mean_window(series["pvt"], t0, t1) if series["pvt"] else (None, 0)
        log_ltg = None
        ltg_vals = [c["roll_ltg"] for c in C.parse_cycles(t.motion_log)
                    if c.get("roll_ltg") is not None and p2[0] <= c["wall"] < stop[0]]
        if ltg_vals:
            log_ltg = st.mean(ltg_vals)
        dx = f"({last.get('dist_x_l'):+.2f},{last.get('dist_x_r'):+.2f})" \
            if last.get("dist_x_l") is not None else "--"
        rows.append([
            t.ts,
            f"{roll_tgt:+.2f}" if roll_tgt is not None else "--",
            f"{chc:+.3f}" if chc is not None else "--",
            f"{log_ltg:+.3f}" if log_ltg is not None else "--",
            f"{ltg_interval:+.3f}" if ltg_interval is not None else "--",
            f"{ltg_stop:+.3f}" + (f" (n={nstop})" if nstop else "") if ltg_stop is not None else "--",
            f"{ht:.3f}" if ht is not None else "--",
            f"{mgl:.4f}" if mgl is not None else "--",
            f"{mgr:.4f}" if mgr is not None else "--",
            f"{mhl:.4f}" if mhl is not None else "--",
            f"{mhr:.4f}" if mhr is not None else "--",
            f"{last['roll_hlr']:+.3f}" if last.get("roll_hlr") is not None else "--",
            f"{last['roll_nav']:+.3f}" if last.get("roll_nav") is not None else "--",
            dx] + man)

    lines = [f"# 阶段二 ground height / ltg 统计（{sel[0].date}）", "",
             "> 区间：阶段二开始（STAGE 1d->2）→ PHASE2-STOP active（不含）；",
             "> chcnav窗口：/chcnav/devpvt roll 同区间均值；log_ltg窗口：周期行 roll=(..., ltg X°) 同区间均值；",
             f"> ltg区间估算：lidar-to-ground = (gh_l-height_l)-(gh_r-height_r) 区间均值差 atan2(·, {LATERAL_SPAN:g}m)；",
             f"> ltg停稳估算：PHASE2-STOP 之后末尾 {STOP_TAIL_N} 点同样式；l−r 与 nav roll 同向。",
             "> 高度差-左/右、人工roll：<root>/stats/stat-<YYYYMMDD>.md 人工测量（lift → atan2((左−右)/1000, 1.6m)）。", ""]
    lines.append(C.align_table(header, rows))
    if skipped:
        lines.append(f"\n> 跳过 {len(skipped)} 个（未进阶段二/无停车/缺话题）：{'、'.join(skipped)}")
    text = "\n".join(lines) + "\n"
    out = C.output_path(args.out, C.ROOT / "reports" / "phase2_stats.md",
                        print_only=args.print_only)
    if out:
        C.write_text(out, text)
        print(f"（{len(rows) - len(skipped)} 行有效，跳过 {len(skipped)}）")
    else:
        print(text)


if __name__ == "__main__":
    main()
