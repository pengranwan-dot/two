#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 motion_control_newdf 控制器 log：周期行 → CSV，事件行 → 打印。

用法:
    python3 <tools>/parse_controller_log.py 154647              # 打印事件时间线
    python3 <tools>/parse_controller_log.py 154647 --cycles-csv /tmp/cycles.csv
    python3 <tools>/parse_controller_log.py 154647 --stage 2 --cycles-csv /tmp/p2.csv
    python3 <tools>/parse_controller_log.py 154647 --field dist_x_l,height_fil --tail 20
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

FIELDS = ["t", "stage", "mode", "v", "steer", "thr", "yaw_err", "lift", "lift_fin",
          "roll_tgt", "roll_nav", "roll_cmd", "roll_hlr", "roll_topline", "roll_ltg",
          "roll_fin", "dd_raw", "dd_fil", "ps", "rect_raw", "rect_fil",
          "dist_x_l", "dist_x_r", "dist_travel", "hdg_raw", "hdg_fil",
          "dxdiff_raw", "dxdiff_fil", "height_target", "height_fil", "height_raw",
          "height_comp", "height_fit", "gh_l", "gh_r", "source", "oil_temp", "temp"]


def flat(cyc):
    row = {}
    for k in FIELDS:
        v = cyc.get(k)
        if k == "gh_l":
            v = (cyc.get("gh") or (None,))[0] if cyc.get("gh") else None
        elif k == "gh_r":
            v = (cyc.get("gh") or (None, None))[1] if cyc.get("gh") else None
        elif k == "source":
            v = cyc.get("source")
            v = ",".join(str(x) for x in v) if v else None
        row[k] = v
    return row


def main():
    ap = argparse.ArgumentParser(description="控制器 log 解析")
    ap.add_argument("test", help="测试时间戳（如 154647）")
    ap.add_argument("--cycles-csv", nargs="?", const="auto", default=None,
                    help="周期行导出 CSV：不给值= <root>/csv/<ts>/controller_cycles.csv；"
                         "- 输出到 stdout；也可给路径")
    ap.add_argument("--stage", default=None, help="只保留某阶段周期行（如 2 或 1a）")
    ap.add_argument("--field", default=None, help="逗号分隔字段，--tail 打印用")
    ap.add_argument("--tail", type=int, default=0, help="打印最后 N 条周期行")
    ap.add_argument("--no-events", action="store_true", help="不打印事件时间线")
    ap.add_argument("--events-all", action="store_true", help="打印全部事件（含 Solve/s2 等噪声行）")
    ap.add_argument("--warn", action="store_true", help="包含 [W] 行")
    ap.add_argument("--root", default=None,
                    help="数据根目录（如 data_1013；默认环境变量 DATA_ROOT 或自动探测）")

    args = ap.parse_args()

    C.init(args.root)

    t = C.resolve_test(args.test)
    if not t.motion_log:
        sys.exit(f"{t.ts} 无控制器 log")
    events = C.log_events(t.motion_log)
    if not args.no_events:
        shown = 0
        print(f"== {t.ts} 事件")
        for kind, wall, d in events:
            txt = d.get("text", "")
            if not args.events_all:
                if any(n in txt for n in ("Solve:", "waiting msg", "[roll_ekf]",
                                          "STARTUP s2 active", "rosbag2 recorder",
                                          "Listening for topics")):
                    continue
                if kind == "note":
                    lv = d.get("level")
                    if lv == "W" and not args.warn:
                        continue
                    if lv == "I" and not args.warn:
                        continue
            shown += 1
            if kind == "stage":
                print(f"  {C.hms(wall)}  STAGE {d['frm']} -> {d['to']}")
            elif kind in ("phase1_stop", "phase2_stop"):
                print(f"  {C.hms(wall)}  {kind.upper()} {d['action']}: {d['detail']}")
            elif kind == "converged":
                print(f"  {C.hms(wall)}  CONVERGED（下发 /control/stop）")
            elif kind == "startup_active":
                print(f"  {C.hms(wall)}  STARTUP active y_R={d['y_R']:+.3f} "
                      f"hdg={d['hdg']:+.3f}° d_head={d['d_head']:+.3f} -> {d['mode']}")
            else:
                print(f"  {C.hms(wall)}  {txt[:100]}")
        if not args.events_all:
            print(f"  （隐藏 Solve/s2 等常规行；--events-all 查看全部 {len(events)} 条）")

    cycles = C.parse_cycles(t.motion_log)
    if args.stage:
        cycles = [c for c in cycles if c["stage"] == args.stage]

    if args.tail:
        fields = [s.strip() for s in args.field.split(",")] if args.field else \
            ["t", "stage", "dist_x_l", "dist_x_r", "height_fil", "roll_nav", "roll_ltg"]
        for c in cycles[-args.tail:]:
            print(f"  t={c['t']:7.1f}s [{c['stage']}]  " + "  ".join(
                f"{k}={c.get(k)}" for k in fields))

    if args.cycles_csv:
        rows = [flat(c) for c in cycles]
        path = args.cycles_csv
        if path == "auto":
            path = str(C.CSV / t.ts / "controller_cycles.csv")
        if path == "-":
            w = csv.DictWriter(sys.stdout, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        else:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows)
            print(f"已写入 {path}（{len(rows)} 行）")


if __name__ == "__main__":
    main()
