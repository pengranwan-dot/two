#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单个测试的完整报告（事件时间线 + 阶段耗时 + 阶段二指标 + 参数/温度）。

用法:
    python3 <tools>/test_report.py 154647
    python3 <tools>/test_report.py 154647 --events-all     # 打印全部事件行
    python3 <tools>/test_report.py 154647 --out /tmp/r.md
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
import metrics as M

LIFT_CONV = 5.0
ROLL_CONV = 5.0


def stage_times(events):
    return {f"{e['frm']}->{e['to']}": w for k, w, e in events if k == "stage"}


def sec(v):
    return f"{v:.1f}" if v is not None else "--"


def main():
    ap = argparse.ArgumentParser(description="单测试报告")
    ap.add_argument("test", help="测试时间戳（如 154647）")
    ap.add_argument("--events-all", action="store_true", help="附全部事件行")
    ap.add_argument("--out", default=None,
                    help="输出 markdown（默认 <root>/reports/test_report_<ts>.md；- 为打印到终端）")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="只打印不写文件（等价 --out -）")
    ap.add_argument("--root", default=None,
                    help="数据根目录（如 data_1013；默认环境变量 DATA_ROOT 或自动探测）")

    args = ap.parse_args()

    C.init(args.root)

    t = C.resolve_test(args.test)
    if not t.motion_log:
        sys.exit(f"{t.ts} 无控制器 log")
    events = C.log_events(t.motion_log)
    d = M.summarize(t)
    lines = [f"# {t.ts} 测试报告", ""]
    meta = [f"- bag: `{t.bag}`" if t.bag else "- bag: 无",
            f"- 控制器 log: `{t.motion_log}`",
            f"- arc log: `{t.arc_log.name}`" if t.arc_log else "- arc log: 无",
            f"- 参数快照: `{t.config.name}`" if t.config else "- 参数快照: 无"]
    if t.bag:
        t0, t1, n = C.bag_duration(t.bag)
        meta.append(f"- bag 时长 {sec(t1 - t0)}s，消息 {n}")
    lines += meta

    nl = C.note_lines(t)
    if nl:
        lines += ["", "## 试验备注"] + [f"- {x}" for x in nl]

    m = C.manual_stats(t)
    if m:
        lines += ["", "## 人工测量（<root>/stats）"]
        if "head" in m:
            lines.append(f"- 车头偏左：{m['head']}mm")
        if "lift_l" in m:
            roll = C.manual_roll_deg(m["lift_l"], m["lift_r"])
            lines.append(f"- lift（高度差左/右）：{m['lift_l']} / {m['lift_r']}mm"
                         + (f"，人工 roll {roll:+.3f}°" if roll is not None else ""))
        if "dist_l" in m:
            lines.append(f"- 车头左右距离：{m['dist_l']} / {m['dist_r']}mm")
        if m.get("time") is not None:
            lines.append(f"- 时间：{m['time']:g}s")
        if m.get("marker"):
            lines.append(f"- 备注：{m['marker']}")

    lines += ["", "## 结果", "",
              f"- 状态：**{d['status']}**，用时 {sec(d.get('elapsed'))}s，"
              f"周期行 {d.get('cycles')} 条（阶段二 {d.get('p2_cycles')}）"]
    if d.get("stop_kind"):
        lines.append(f"- 停车触发（{d['stop_kind']}）：{d.get('stop_trigger')}")
    ini = d.get("initial")
    if ini:
        src = "log STARTUP active" if ini["src"] == "log" else "bag 感知复算"
        lines.append(f"- 初始偏差（{src}）：y_R={ini['y_R']:+.3f}，"
                     f"hdg={ini['hdg']:+.3f}°，d_head={ini['d_head']:+.3f}"
                     + (f"，策略 {ini['mode']}" if ini.get("mode") else ""))

    st = stage_times(events)
    order = ["init->1a", "1a->1b", "1b->1c", "1c->1d", "1d->2"]
    if any(k in st for k in order):
        lines += ["", "## 阶段时刻（相对 init->1a）", ""]
        t0 = st.get("init->1a")
        for k in order:
            if k in st and t0 is not None:
                lines.append(f"- {k}: {sec((st[k] - t0) % 86400)}s")
        p2 = st.get("1d->2")
        if p2 is not None and d.get("p2_end"):
            lines.append(f"- 阶段二: {sec((d['p2_end'] - p2) % 86400)}s"
                         f"（->{sec((d['t_end'] - t0) % 86400)}s log 结束）")

    if d.get("p2_last"):
        last = d["p2_last"]
        lines += ["", "## 阶段二末次周期", ""]
        for k, lab, spec in [("dist_x_l", "dist_x L", "+.3f"), ("dist_x_r", "dist_x R", "+.3f"),
                             ("dist_travel", "行驶距离", ".3f"),
                             ("height_target", "height target", ".3f"),
                             ("height_fil", "height 滤波", ".3f"),
                             ("roll_tgt", "roll target", "+.3f"),
                             ("roll_nav", "nav roll", "+.3f"),
                             ("roll_ltg", "ltg roll", "+.3f"),
                             ("roll_hlr", "hlr roll", "+.3f"),
                             ("dd_fil", "distance_diff 滤波", "+.3f"),
                             ("hdg_fil", "航向修正", "+.3f"),
                             ("steer", "steer", "+.2f"),
                             ("thr", "throttle", ".0f")]:
            if last.get(k) is not None:
                fmt = "{" + f":{spec}" + "}"
                lines.append(f"- {lab}: {fmt.format(last[k])}")
        lc = abs(last["lift_fin"]) <= LIFT_CONV if last.get("lift_fin") is not None else None
        rc = abs(last["roll_fin"]) <= ROLL_CONV if last.get("roll_fin") is not None else None
        lines.append(f"- 收敛：lift {'是' if lc else '否'}"
                     f"（fin {last.get('lift_fin')}），"
                     f"roll {'是' if rc else '否'}（fin {last.get('roll_fin')}）")

    if d.get("p2_stop_window"):
        w = d["p2_stop_window"]
        lines += ["", f"## 停车窗口均值（PHASE2-STOP，n={w['n']}）", ""]
        lines.append("- dist_x L/R: " + (f"{w['dist_x_l']:+.3f} / {w['dist_x_r']:+.3f}"
                                         if w.get("dist_x_l") is not None else "--"))
        lines.append("- height 滤波: " + (f"{w['height_fil']:.3f}" if w.get("height_fil") is not None else "--"))
        lines.append("- nav roll: " + (f"{w['roll_nav']:+.3f}°" if w.get("roll_nav") is not None else "--")
                     + "，ltg roll: " + (f"{w['roll_ltg']:+.3f}°" if w.get("roll_ltg") is not None else "--"))
        lines.append("- steer 均值: " + (f"{w['steer']:+.2f}°" if w.get("steer") is not None else "--"))

    lines += ["", "## 温度", "",
              f"- IMU（后 30% 均值）：{d['temp']:.1f}°C" if d.get("temp") is not None else "- IMU: --",
              f"- 液压油箱：{d['oil_temp']:.1f}°C" if d.get("oil_temp") is not None else "- 液压油箱: --",
              f"- roll_target（众数）：{d['roll_tgt']:+.3f}°" if d.get("roll_tgt") is not None else "- roll_target: --"]

    if args.events_all:
        lines += ["", "## 事件行", ""]
        for kind, wall, e in events:
            lines.append(f"- {wall:.2f} [{kind}] {e.get('text', '')}")

    text = "\n".join(lines) + "\n"
    out = C.output_path(args.out, C.ROOT / "reports" / f"test_report_{t.ts}.md",
                        print_only=args.print_only)
    if out:
        C.write_text(out, text)
    else:
        print(text)


if __name__ == "__main__":
    main()
