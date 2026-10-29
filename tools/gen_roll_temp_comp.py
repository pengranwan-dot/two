#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根据多天试验数据生成 roll 分段温度补偿参数（兼容旧 scripts/startup_strategy/gen_roll_temp_comp.py）。

原理: 汇聚各测试样本 (温度, roll_target, |lift_l − lift_r|)，按温度分段，
每段取 |高度差| 均值最小的 roll_target 为最优；补偿 = 最优 − 基准(--base)；
相邻段最优值相同则合并为一个区间。

数据来源（新目录结构，可多个 root 合并）：
  - 人工高度差: <root>/stats/stat-<date>.md（lift X Y）
  - roll_target: 控制器 log 周期行 `roll=(tgt …)` 实测众数
  - 温度: --temp-source imu(log temp=) / oil(log oil_temp=) / env(/temperature_env)

用法:
    python3 tools/gen_roll_temp_comp.py --root data_1015
    python3 tools/gen_roll_temp_comp.py --root data_1013 --root data_1015 --temp-source oil
    python3 tools/gen_roll_temp_comp.py --root data_1012 --root data_1014 --base -0.18 --bin 5
    python3 tools/gen_roll_temp_comp.py --root data_1015 --print      # 只打印不写文件
输出: 默认写 <第一个 root>/reports/roll_temp_comp.md（--out 可改，- 打印到终端）
"""
import argparse
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import metrics as M


def collect(root, temp_source="imu"):
    """返回 [(temp, roll_target, |lift_l−lift_r|, ts)] 及统计信息。"""
    C.init(root)
    rows, skipped = [], []
    for ts, t in C.discover_tests().items():
        m = C.manual_stats(t) or {}
        if m.get("lift_l") is None or m.get("lift_r") is None:
            skipped.append(f"{ts}（无 lift 测量）")
            continue
        d = M.summarize(t, bag_fallback=False)
        rt = d.get("roll_tgt")
        if rt is None:
            skipped.append(f"{ts}（无 roll_target）")
            continue
        if temp_source == "oil":
            temp = d.get("oil_temp")
            unit = "液压油温"
        elif temp_source == "env":
            s = C.load_series(t, "/temperature_env")
            temp = st.mean(v for _, v in s) if s else None
            unit = "环境温度"
        else:
            temp = d.get("temp")
            unit = "IMU温度"
        if temp is None:
            skipped.append(f"{ts}（无{unit}）")
            continue
        rows.append((temp, rt, abs(m["lift_l"] - m["lift_r"]), ts))
    return rows, skipped, unit


def gen_comp(rows, base, bin_w, min_n):
    if not rows:
        raise SystemExit("error: 无有效数据")
    tmin = min(r[0] for r in rows)
    tmax = max(r[0] for r in rows)
    bins = defaultdict(list)
    for t, rt, dh, ts in rows:
        bins[int(t // bin_w) * bin_w].append((t, rt, dh))

    best = []
    for seg in sorted(bins):
        by_rt = defaultdict(list)
        for t, rt, dh in bins[seg]:
            by_rt[rt].append(dh)
        cand = [(st.mean(v), len(v), rt) for rt, v in by_rt.items() if len(v) >= min_n]
        if not cand:
            cand = [(st.mean(v), len(v), rt) for rt, v in by_rt.items()]
        m, n, rt = min(cand)
        best.append([seg, seg + bin_w, rt, n, m,
                     sorted((r, len(v)) for r, v in by_rt.items())])

    merged = []
    for seg in best:
        if merged and abs(merged[-1][2] - seg[2]) < 1e-9:
            merged[-1][1] = seg[1]
            merged[-1][3] += seg[3]
        else:
            merged.append([seg[0], seg[1], seg[2], seg[3], seg[4], list(seg[5])])

    segments = [m[1] for m in merged[:-1]]
    comps = [round(m[2] - base, 4) for m in merged]
    return best, merged, segments, comps, tmin, tmax


def build_report(roots, unit, rows, skipped, best, merged, segments, comps,
                 tmin, tmax, base, base_src, bin_w, min_n):
    targets = sorted({r[1] for r in rows})
    lines = ["# roll 温度补偿参数生成", "",
             f"> 数据: {len(rows)} 次（{len(roots)} 个数据根目录：{', '.join(roots)}）",
             f"> 温度来源: {unit}；范围 {tmin:.1f}~{tmax:.1f}°C；分段宽度 {bin_w:g}°C、"
             f"每段最小样本 {min_n}",
             f"> 基准 roll_target = {base:+.3f}（{base_src}）",
             f"> 样本 roll_target 取值: {', '.join(f'{x:+.3f}' for x in targets)}", ""]
    if len(targets) < 2:
        lines += [f"> ⚠️ **警告：样本中 roll_target 只有 1 种取值（{targets[0]:+.3f}），"
                  "无法在各温度段寻找更优值；需要包含多种 roll_target 的试验数据"
                  "（历轮不同参数或多天不同设置）。**", ""]

    lines += ["## 各温度段最优 roll_target", "",
              "| 温度段(°C) | 最优 roll_target | n | 平均\\|高度差\\|(mm) | 段内 roll_target(n) |",
              "|---|---|---|---|---|"]
    for seg in best:
        detail = "、".join(f"{rt:+.2f}(n={n})" for rt, n in seg[5])
        lines.append(f"| {seg[0]:g}~{seg[1]:g} | **{seg[2]:+.2f}** | {seg[3]} | {seg[4]:.2f} | {detail} |")

    lines += ["", "## 合并后分段", "",
              "| 温度区间(°C) | 最优 roll_target | 补偿(deg) |",
              "|---|---|---|"]
    for m in merged:
        lines.append(f"| {m[0]:g}~{m[1]:g} | {m[2]:+.3f} | {m[2] - base:+.3f} |")

    lines += ["", "## 建议 yaml 参数", "", "```yaml",
              f"roll_temp_comp_segments: [{', '.join(f'{s:g}' for s in segments)}]   # 分段点(°C)",
              f"roll_temp_comp_values: [{', '.join(f'{c:+.3f}' for c in comps)}]   # 各区间补偿(deg)，基准 {base:+.3f}",
              "```", ""]
    if skipped:
        lines += ["## 跳过", ""] + [f"- {x}" for x in skipped]
    return "\n".join(lines).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser(description="roll 分段温度补偿参数生成")
    ap.add_argument("--root", action="append", default=None,
                    help="数据根目录，可重复指定多个（默认自动探测单个）")
    ap.add_argument("--temp-source", default="imu", choices=["imu", "oil", "env"],
                    help="温度来源: imu=log temp(默认) / oil=液压油温 / env=/temperature_env")
    ap.add_argument("--base", type=float, default=None,
                    help="补偿基准 roll_target（默认取样本众数）")
    ap.add_argument("--bin", type=float, default=5.0, help="温度分段宽度 °C（默认 5）")
    ap.add_argument("--min-n", type=int, default=3, help="段内采信最小样本数（默认 3）")
    ap.add_argument("--out", default=None,
                    help="输出文件（默认 <第一个 root>/reports/roll_temp_comp.md；- 打印）")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="只打印不写文件（等价 --out -）")
    args = ap.parse_args()

    roots = args.root
    if not roots:
        C.init()  # 触发自动探测/报错
        roots = [str(C.ROOT)]

    all_rows, all_skipped = [], []
    unit = "IMU温度"
    for r in roots:
        rows, skipped, unit = collect(r, args.temp_source)
        all_rows += rows
        all_skipped += [f"{Path(r).name}: {s}" for s in skipped]
    if not all_rows:
        raise SystemExit("error: 无有效数据（检查 --root 与 stats 人工测量）")

    if args.base is None:
        base = st.mode([round(r[1], 3) for r in all_rows])
        base_src = "样本众数自动"
    else:
        base = args.base
        base_src = "--base 指定"

    best, merged, segments, comps, tmin, tmax = gen_comp(
        all_rows, base, args.bin, args.min_n)
    report = build_report(roots, unit, all_rows, all_skipped, best, merged,
                          segments, comps, tmin, tmax, base, base_src, args.bin, args.min_n)

    out = C.output_path(args.out, Path(roots[0]) / "reports" / "roll_temp_comp.md",
                        print_only=args.print_only)
    if out:
        C.write_text(out, report)
    else:
        print(report)


if __name__ == "__main__":
    main()
