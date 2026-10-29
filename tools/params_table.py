#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参数与温度记录表（兼容旧 scripts/gen_params_table.py）。

对每个测试输出：IMU/油温（log 后 30% 均值）、roll_target（log 众数）、
phase2 height target（log 众数）、height 温度补偿（config）、阶段二末 nav/ltg/hlr/height、
lift/roll 收敛判断、chcnav 停车窗口均值、生效参数快照；附快照参数变化。

用法:
    python3 <tools>/params_table.py                          # 全部测试
    python3 <tools>/params_table.py --date 20261012
    python3 <tools>/params_table.py --from 170000 --to 194313
    python3 <tools>/params_table.py --out <root>/params_table.md
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import metrics as M
import config_diff as CD

LIFT_CONV = 5.0
ROLL_CONV = 5.0


def height_comp(temp, cfg):
    if not cfg.get("apply"):
        return 0.0
    segs, vals = cfg.get("segments") or [], cfg.get("values") or []
    if not segs or not vals:
        return 0.0
    comp = vals[-1]
    for s, v in zip(segs, vals):
        if temp < s:
            comp = v
            break
    return comp


def read_height_cfg(test):
    path = C.config_yaml(test)
    out = {"apply": False}
    if not path or not path.exists():
        return out
    txt = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"height_temp_comp_apply:\s*(true|false)", txt)
    out["apply"] = bool(m and m.group(1) == "true")
    m = re.search(r"height_temp_comp_segments:\s*\[([\d.,\s]*)\]", txt)
    if m:
        out["segments"] = [float(x) for x in m.group(1).split(",") if x.strip()]
    m = re.search(r"height_temp_comp_values:\s*\[([\d.,\s\-]*)\]", txt)
    if m:
        out["values"] = [float(x) for x in m.group(1).split(",") if x.strip()]
    return out


def chcnav_stop_mean(test, d):
    if not test.bag:
        return None
    ev = d.get("events") or {}
    p2 = ev.get("phase2_stop_active")
    if not p2:
        return None
    w0 = C.wall_to_epoch(test, p2[0])
    p2d = ev.get("phase2_stop_done")
    w1 = C.wall_to_epoch(test, p2d[0]) if p2d else C.wall_to_epoch(test, d.get("t_end"))
    if w0 is None or w1 is None or w1 <= w0:
        return None
    series = C.load_series(test, "/chcnav/devpvt")
    mean, n = C.mean_window(series, w0, w1)
    return mean if mean is not None and n >= 20 else None


def main():
    ap = argparse.ArgumentParser(description="参数与温度记录表")
    ap.add_argument("--date", default=None, help="只统计某天 YYYYMMDD")
    ap.add_argument("--from", dest="from_ts", default=None, help="起始时间戳 HHMMSS")
    ap.add_argument("--to", dest="to_ts", default=None, help="结束时间戳 HHMMSS")
    ap.add_argument("--out", default=None,
                    help="输出 markdown（默认 <root>/reports/params_table.md；- 为打印到终端）")
    ap.add_argument("--no-chcnav", action="store_true", help="跳过 chcnav 停车窗口均值")
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
    if not sel:
        sys.exit("无匹配测试")

    header = ["测试", "快照", "IMU(°C)", "油温(°C)", "roll_target(°)",
              "h target(m)", "h补偿(m)", "补偿后(m)",
              "高度差-左(mm)", "高度差-右(mm)", "高度差差(mm)", "target+高度差补偿(°)",
              "末nav(°)", "末ltg(°)", "末hlr(°)", "末height(m)", "lift收敛", "roll收敛",
              "chcnav停稳(°)", "chcnav+高度差补偿(°)"]
    rows = []
    for t in sel:
        d = M.summarize(t, bag_fallback=False)
        cfg = read_height_cfg(t)
        ht = d.get("height_target")
        comp = height_comp(d.get("temp"), cfg) if d.get("temp") is not None else None
        comp_txt = "--"
        after = "--"
        if comp is not None:
            comp_txt = f"{comp:+.3f}" if comp else "0"
            if ht is not None:
                after = f"{ht + comp:.3f}"
        last = d.get("p2_last") or {}
        conv = ""
        lc = last.get("lift_fin")
        rc = last.get("roll_fin")
        conv_l = "是" if lc is not None and abs(lc) <= LIFT_CONV else ("否" if lc is not None else "--")
        conv_r = "是" if rc is not None and abs(rc) <= ROLL_CONV else ("否" if rc is not None else "--")
        chc = None if args.no_chcnav else chcnav_stop_mean(t, d)
        m = C.manual_stats(t) or {}
        comp_deg = None
        if m.get("lift_l") is not None and m.get("lift_r") is not None:
            comp_deg = -0.01 * (m["lift_l"] - m["lift_r"])
        nav_comp = (d["roll_tgt"] + comp_deg
                    if d.get("roll_tgt") is not None and comp_deg is not None else None)
        chc_comp = (chc + comp_deg if chc is not None and comp_deg is not None else None)
        rows.append([
            t.ts, t.config.name.replace("autonomous_config-", "") if t.config else "--",
            f"{d['temp']:.1f}" if d.get("temp") is not None else "--",
            f"{d['oil_temp']:.1f}" if d.get("oil_temp") is not None else "--",
            f"{d['roll_tgt']:+.2f}" if d.get("roll_tgt") is not None else "--",
            f"{ht:.3f}" if ht is not None else "--",
            comp_txt, after,
            C.fmt(m.get("lift_l")), C.fmt(m.get("lift_r")),
            C.fmt(m["lift_l"] - m["lift_r"]) if comp_deg is not None else "--",
            f"{nav_comp:+.3f}" if nav_comp is not None else "--",
            f"{last['roll_nav']:+.3f}" if last.get("roll_nav") is not None else "--",
            f"{last['roll_ltg']:+.3f}" if last.get("roll_ltg") is not None else "--",
            f"{last['roll_hlr']:+.3f}" if last.get("roll_hlr") is not None else "--",
            f"{last['height_fil']:.3f}" if last.get("height_fil") is not None else "--",
            conv_l, conv_r,
            f"{chc:+.3f}" if chc is not None else "--",
            f"{chc_comp:+.3f}" if chc_comp is not None else "--"])

    temps = [d for d in (float(r[2]) for r in rows if r[2] != "--")]
    oils = [d for d in (float(r[3]) for r in rows if r[3] != "--")]
    lines = [f"# 参数与温度记录表（{sel[0].date}，n={len(rows)}）", "",
             "> 数据源：<root>/logs/motion_control_newdf_<ts>.log + <root>/configs 快照 + <root>/bags chcnav",
             "> - IMU/油温：log 周期行后 30% 均值；roll_target / h target：log 周期行众数",
             "> - 末 nav/ltg/hlr/height：阶段二末次周期行；lift/roll 收敛：|fin|≤5 判是",
             "> - chcnav停稳：PHASE2-STOP active→done 窗口 /chcnav/devpvt roll 均值",
             "> - h补偿：height_temp_comp（按 IMU 温度分段）；补偿后 = h target + 补偿",
             "> - 高度差：<root>/stats 人工测量；高度差差 = 左−右；补偿 = −0.01°/mm×高度差差", ""]
    lines.append(C.align_table(header, rows))
    if temps:
        lines.append(f"\n- IMU 温度：{min(temps):.1f} → {max(temps):.1f}°C")
    if oils:
        lines.append(f"- 液压油箱温度：{min(oils):.1f} → {max(oils):.1f}°C")

    snaps = CD.snapshots()
    if snaps:
        lines += ["", "## 参数快照变化", ""]
        prev_params = prev_label = None
        for ts, snap in snaps:
            path = snap / CD.DEFAULT_FILE
            if not path.exists():
                continue
            params = CD.parse_yaml_params(path)
            if prev_params is not None:
                changes = CD.diff_params(params, prev_params)
                if changes:
                    lines += [f"### {ts}（相对 {prev_label}）", "",
                              "```yaml", CD.render_changes(changes, nest=True), "```", ""]
            prev_params, prev_label = params, ts

    text = "\n".join(lines) + "\n"
    out = C.output_path(args.out, C.ROOT / "reports" / "params_table.md",
                        print_only=args.print_only)
    if out:
        C.write_text(out, text)
        print(f"（{len(rows)} 行）")
    else:
        print(text)


if __name__ == "__main__":
    main()
