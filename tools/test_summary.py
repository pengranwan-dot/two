#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全部测试自动汇总：自动指标表 + 人工测量表 + 达成率统计。

自动指标（来自控制器 log）：
  用时 / 结果 / 初始航向与横向偏差 / 阶段二停车触发 / 阶段二末次 dist_x / height /
  nav roll / ltg / IMU 与油温 / 试验备注（note-<YYYYMMDD>.md）。
人工测量（来自 <root>/stats/stat-<YYYYMMDD>.md 各 `## <ts>` 小节）：
  车头偏左 / lift(高度差左,右) / 车头左右距离 / 时间，及分项达成率与全部指标达成率。

用法:
    python3 <tools>/test_summary.py
    python3 <tools>/test_summary.py --date 20261012 --out <root>/summary.md
    python3 <tools>/test_summary.py --from 170000 --to 194313 --exclude 190233
    python3 <tools>/test_summary.py --thresholds lat=10,height=5,dist_min=120,dist_max=180,time_max=120
    python3 <tools>/test_summary.py --flip-lateral-error     # 车头偏左正负号反转
    python3 <tools>/test_summary.py --detail                 # 未达标明细
    python3 <tools>/test_summary.py --no-bag-fallback        # 不用 bag 复算初始偏差
"""
import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import metrics as M

MANUAL_COLS = ["车头偏左", "高度差-左", "高度差-右", "停车距离-左", "停车距离-右", "时间"]


def short(text, n=60):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def build_auto(sel, bag_fallback=True):
    header = ["测试", "用时(s)", "结果", "初始hdg(°)", "初始d_head", "停车触发",
              "阶段二末 dist_x L/R(m)", "末 height target/fil(m)", "末 nav roll(°)",
              "末 ltg(°)", "IMU(°C)", "油温(°C)", "备注"]
    rows = []
    for t in sel:
        d = M.summarize(t, bag_fallback=bag_fallback)
        ini = d.get("initial")
        last = d.get("p2_last") or {}
        dx = f"({last.get('dist_x_l'):+.2f},{last.get('dist_x_r'):+.2f})" \
            if last.get("dist_x_l") is not None else "--"
        ht = f"{last.get('height_target'):.3f}/{last.get('height_fil'):.3f}" \
            if last.get("height_target") is not None and last.get("height_fil") is not None else "--"
        rows.append([
            t.ts, f"{d['elapsed']:.1f}" if d.get("elapsed") is not None else "--",
            d["status"],
            f"{ini['hdg']:+.2f}" if ini else "-",
            f"{ini['d_head']:+.3f}" if ini else "-",
            short(d.get("stop_trigger"), 26) or "--", dx, ht,
            f"{last['roll_nav']:+.3f}" if last.get("roll_nav") is not None else "--",
            f"{last['roll_ltg']:+.3f}" if last.get("roll_ltg") is not None else "--",
            f"{d['temp']:.1f}" if d.get("temp") is not None else "--",
            f"{d['oil_temp']:.1f}" if d.get("oil_temp") is not None else "--",
            short("；".join(d.get("notes") or []), 48)])
    return header, rows


def build_manual(sel, flip_lat=False):
    header = ["测试"] + MANUAL_COLS + ["人工roll(°)"]
    rows, missing = [], []
    for t in sel:
        m = C.manual_stats(t) or {}
        if "head" not in m and "lift_l" not in m:
            missing.append((t.ts, m.get("marker") or "未测量"))
            continue
        head = m.get("head")
        if head is None:
            missing.append((t.ts, m.get("marker") or "不完整"))
            continue
        if flip_lat:
            head = -head
        roll = C.manual_roll_deg(m.get("lift_l"), m.get("lift_r"))
        rows.append([
            t.ts, str(head),
            C.fmt(m.get("lift_l")), C.fmt(m.get("lift_r")),
            C.fmt(m.get("dist_l")), C.fmt(m.get("dist_r")),
            f"{m['time']:g}" if m.get("time") is not None else "-",
            f"{roll:+.3f}" if roll is not None else "--"])
    return header, rows, missing


def compute_stats(rows, th, detail=False):
    lat_t, hgt_t, dmin, dmax, tmax = th
    n = len(rows)
    head = [int(r[1]) for r in rows]
    hl = [int(r[2]) for r in rows]
    hr = [int(r[3]) for r in rows]
    dl = [int(r[4]) for r in rows]
    dr = [int(r[5]) for r in rows]
    times = [float(r[6]) for r in rows if r[6] != "-"]

    def pct(x, total):
        return 100.0 * x / total if total else 0.0

    lat_ok = sum(1 for r in rows if abs(int(r[1])) <= lat_t)
    h5l = sum(1 for x in hl if abs(x) <= hgt_t)
    h5r = sum(1 for x in hr if abs(x) <= hgt_t)
    hgt_ok = sum(1 for r in rows if abs(int(r[2])) <= hgt_t and abs(int(r[3])) <= hgt_t)
    dist_ok = sum(1 for r in rows
                  if dmin <= int(r[4]) <= dmax and dmin <= int(r[5]) <= dmax)
    time_ok = sum(1 for r in rows if r[6] != "-" and float(r[6]) <= tmax)
    all_ok = sum(1 for r in rows if all([
        abs(int(r[1])) <= lat_t,
        abs(int(r[2])) <= hgt_t and abs(int(r[3])) <= hgt_t,
        dmin <= int(r[4]) <= dmax and dmin <= int(r[5]) <= dmax,
        r[6] != "-" and float(r[6]) <= tmax]))

    lines = [f"### 统计小结（n={n}）", ""]
    lat15 = sum(1 for x in head if abs(x) <= lat_t * 1.5)
    over = [(r[0], int(r[1])) for r in rows if abs(int(r[1])) > lat_t]
    sd = st.stdev(head) if len(head) > 1 else 0.0
    lines.append(
        f"- 车头偏左：均值 {st.mean(head):.1f}mm，标准差 {sd:.1f}mm，"
        f"范围 [{min(head)}, {max(head)}]mm；|x|≤{lat_t:g}mm 共 {lat_ok}/{n}"
        f"（{pct(lat_ok, n):.1f}%），≤{lat_t * 1.5:g}mm 共 {lat15}/{n}；"
        f"超限项：{'、'.join(f'{t}（{v:+d}）' for t, v in over) if over else '无'}。")
    lines.append(
        f"- 高度差：左均值 {st.mean(hl):.1f}mm（|x|≤{hgt_t:g}mm {h5l}/{n}），"
        f"右均值 {st.mean(hr):.1f}mm（|x|≤{hgt_t:g}mm {h5r}/{n}），"
        f"两侧同时满足 {hgt_ok}/{n}（{pct(hgt_ok, n):.1f}%）。")
    lines.append(
        f"- 停车距离：左 [{min(dl)}, {max(dl)}]mm、均值 {st.mean(dl):.1f}mm；"
        f"右 [{min(dr)}, {max(dr)}]mm、均值 {st.mean(dr):.1f}mm；"
        f"两侧∈[{dmin:g},{dmax:g}]mm 共 {dist_ok}/{n}（{pct(dist_ok, n):.1f}%）。")
    crit = (f"车头偏左≤{lat_t:g}mm 且 高度差两侧≤{hgt_t:g}mm 且 "
            f"停车距离两侧∈[{dmin:g},{dmax:g}]mm 且 时间≤{tmax:g}s")
    parts = [f"车头偏左 {pct(lat_ok, n):.1f}%（{lat_ok}/{n}）",
             f"高度差两侧 {pct(hgt_ok, n):.1f}%（{hgt_ok}/{n}）",
             f"停车距离两侧 {pct(dist_ok, n):.1f}%（{dist_ok}/{n}）"]
    if times:
        parts.append(f"时间≤{tmax:g}s {pct(time_ok, n):.1f}%（{time_ok}/{n}）")
    lines.append(f"- **全部指标达成率**（{crit} 同时满足）：**{all_ok}/{n} = "
                 f"{pct(all_ok, n):.1f}%**。分项：{'、'.join(parts)}。")
    if times:
        lines.append(f"- 人工时间：均值 {st.mean(times):.0f}s，"
                     f"范围 [{min(times):g}, {max(times):g}]s（n={len(times)}）。")
    if detail:
        fails = []
        for r in rows:
            bad = []
            if abs(int(r[1])) > lat_t:
                bad.append("偏左")
            if abs(int(r[2])) > hgt_t or abs(int(r[3])) > hgt_t:
                bad.append("高度差")
            if not (dmin <= int(r[4]) <= dmax and dmin <= int(r[5]) <= dmax):
                bad.append("停车距离")
            if r[6] == "-" or float(r[6]) > tmax:
                bad.append("时间")
            if bad:
                fails.append(f"{r[0]}（{'/'.join(bad)}：偏左{r[1]} "
                             f"lift({r[2]},{r[3]}) 距离({r[4]},{r[5]}) 时间{r[6]}）")
        if fails:
            lines += ["", "未达标明细："] + [f"- {x}" for x in fails]
    return lines


def parse_thresholds(text):
    th = {}
    for kv in text.split(","):
        k, v = kv.split("=")
        th[k.strip()] = float(v)
    return (th["lat"], th["height"], th["dist_min"], th["dist_max"], th.get("time_max", 120.0))


def main():
    ap = argparse.ArgumentParser(description="测试汇总（自动指标 + 人工测量）")
    ap.add_argument("--date", default=None, help="只统计某天 YYYYMMDD")
    ap.add_argument("--from", dest="from_ts", default=None, help="起始时间戳（HHMMSS）")
    ap.add_argument("--to", dest="to_ts", default=None, help="结束时间戳（HHMMSS）")
    ap.add_argument("--exclude", default="", help="逗号分隔排除的时间戳子串")
    ap.add_argument("--out", default=None,
                    help="输出 markdown（默认 <root>/reports/test_summary.md；- 为打印到终端）")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="只打印不写文件（等价 --out -）")
    ap.add_argument("--no-bag-fallback", action="store_true", help="不用 bag 复算初始偏差")
    ap.add_argument("--manual-only", action="store_true", help="只输出人工测量表与统计")
    ap.add_argument("--thresholds",
                    default="lat=10,height=5,dist_min=120,dist_max=180,time_max=120",
                    help="人工测量指标阈值")
    ap.add_argument("--flip-lateral-error", action="store_true",
                    help="车头偏左数据正负号反转")
    ap.add_argument("--detail", action="store_true", help="附未达标明细")
    ap.add_argument("--root", default=None,
                    help="数据根目录（如 data_1013；默认环境变量 DATA_ROOT 或自动探测）")

    args = ap.parse_args()

    C.init(args.root)

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
    if not sel:
        sys.exit("无匹配测试")

    lines = [f"# 测试汇总（{sel[0].date}，n={len(sel)}）", ""]

    if not args.manual_only:
        header, rows = build_auto(sel, bag_fallback=not args.no_bag_fallback)
        lines += ["## 自动指标（控制器 log）", "", C.align_table(header, rows), ""]
        details = [M.summarize(t, bag_fallback=False) for t in sel]
        ok = sum(1 for d in details if d["status"] == "成功")
        lines += [f"- 成功 {ok}/{len(rows)}；"
                  f"未停车 {sum(1 for d in details if d['status'] == '未停车')}；"
                  f"未启动 {sum(1 for d in details if d['status'] == '未启动(无STAGE)')}；"
                  f"无 log {sum(1 for d in details if d['status'] == '无log')}", ""]
        glob = []
        seen = set()
        for t in sel:
            for ln in C.note_global_lines(t):
                if ln and ln not in seen:
                    seen.add(ln)
                    glob.append(ln)
        if glob:
            lines += ["### 试验记录（note 非时间戳行）", ""] + [f"- {ln}" for ln in glob] + [""]

    if C.STATS.is_dir():
        header, rows, missing = build_manual(sel, flip_lat=args.flip_lateral_error)
        lines += ["## 人工测量（<root>/stats）", ""]
        if rows:
            lines += [C.align_table(header, rows), ""]
            lines += compute_stats(rows, parse_thresholds(args.thresholds), detail=args.detail)
        else:
            lines.append("（无有效人工测量数据）")
        if missing:
            lines += ["", "未列入（未测量/异常）："
                          + "、".join(f"{ts}（{mk}）" for ts, mk in missing)]
    else:
        lines += [f"（未找到 {C.STATS}，跳过人工测量）"]

    text = "\n".join(lines) + "\n"
    out = C.output_path(args.out, C.ROOT / "reports" / "test_summary.md",
                        print_only=args.print_only)
    if out:
        C.write_text(out, text)
        print(f"（{len(sel)} 个测试；--print 可只看终端输出）")
    else:
        print(text)


if __name__ == "__main__":
    main()
