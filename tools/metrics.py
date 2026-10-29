#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单测试指标提取（test_summary.py / test_report.py / params_table.py / phase2_stats.py 共用）。"""
import statistics as st

import common as C

P2_STAGE_CYCLE_KEY = {
    "dd_raw": "dd_raw", "dd_fil": "dd_fil", "rect_fil": "rect_fil",
    "dist_x_l": "dist_x_l", "dist_x_r": "dist_x_r", "dist_travel": "dist_travel",
    "hdg_fil": "hdg_fil", "dxdiff_fil": "dxdiff_fil",
    "height_target": "height_target", "height_fil": "height_fil",
    "height_raw": "height_raw", "height_comp": "height_comp",
    "roll_nav": "roll_nav", "roll_ltg": "roll_ltg", "roll_hlr": "roll_hlr",
    "lift_fin": "lift_fin", "roll_fin": "roll_fin", "steer": "steer",
    "thr": "thr", "v": "v", "lift": "lift", "ps": "ps",
}


def tail_mean(xs, frac=0.3):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    t = xs[max(0, int(len(xs) * (1 - frac))):]
    return st.mean(t)


def summarize(test, bag_fallback=True):
    d = {"ts": test.ts, "status": "unknown", "events": {}}
    if not test.motion_log:
        d["status"] = "无log"
        return d
    events = C.log_events(test.motion_log)
    cycles = C.parse_cycles(test.motion_log)
    d["cycles"] = len(cycles)
    d["t_end"] = cycles[-1]["wall"] if cycles else None

    ev = d["events"]
    for kind, wall, e in events:
        if kind == "stage":
            ev.setdefault(f"stage_{e['frm']}_{e['to']}", (wall, e))
        elif kind in ("phase1_stop", "phase2_stop"):
            ev.setdefault(f"{kind}_{e['action']}", (wall, e))
        elif kind == "converged":
            ev.setdefault("converged", (wall, e))
        elif kind == "startup_active":
            ev.setdefault("startup_active", (wall, e))
        elif kind == "startup_done":
            ev.setdefault("startup_done", (wall, e))

    t_start = ev.get("stage_init_1a", (None,))[0]
    t_conv = ev.get("converged", (None,))[0]
    if t_start is not None and t_conv is not None:
        d["elapsed"] = (t_conv - t_start) % 86400
    elif t_start is not None and d["t_end"]:
        d["elapsed"] = (d["t_end"] - t_start) % 86400
    else:
        d["elapsed"] = None

    if t_start is None:
        d["status"] = "未启动(无STAGE)"
    elif t_conv is None:
        d["status"] = "未停车"
    else:
        d["status"] = "成功"

    sa = ev.get("startup_active")
    if sa:
        e = sa[1]
        d["initial"] = {"hdg": e["hdg"], "d_head": e["d_head"], "y_R": e["y_R"],
                        "mode": e["mode"], "samples": e["samples"], "src": "log"}
    else:
        d["initial"] = None
    if d["initial"] is None and bag_fallback and test.bag:
        rows = C.initial_deviations(test)
        if rows:
            w = rows[:5]
            d["initial"] = {
                "hdg": st.mean(r["hdg"] for r in w),
                "d_head": st.mean(r["d_head"] for r in w),
                "y_R": st.mean(r["y_R"] for r in w),
                "mode": None, "samples": len(w), "src": "bag"}

    p2 = ev.get("phase2_stop_active")
    p2d = ev.get("phase2_stop_done")
    p1 = ev.get("phase1_stop_active")
    if p2:
        d["stop_trigger"] = p2[1]["detail"]
        d["stop_kind"] = "phase2"
    elif p1:
        d["stop_trigger"] = p1[1]["detail"]
        d["stop_kind"] = "phase1"
    else:
        d["stop_trigger"] = None
        d["stop_kind"] = None

    d["temp"] = tail_mean([c.get("temp") for c in cycles])
    d["oil_temp"] = tail_mean([c.get("oil_temp") for c in cycles])
    d["roll_tgt"] = st.mode([c["roll_tgt"] for c in cycles if c.get("roll_tgt") is not None]) \
        if any(c.get("roll_tgt") is not None for c in cycles) else None
    d["height_target"] = st.mode([c["height_target"] for c in cycles
                                  if c.get("height_target") is not None]) \
        if any(c.get("height_target") is not None for c in cycles) else None

    p2c = [c for c in cycles if c["stage"] == "2"]
    d["p2_cycles"] = len(p2c)
    d["p2_start"] = ev.get("stage_1d_2", (None,))[0]
    if p2d:
        d["p2_end"] = p2d[0]
    else:
        d["p2_end"] = d["t_end"]
    if p2c:
        d["p2_last"] = p2c[-1]
        tail = p2c[-10:] if len(p2c) >= 10 else p2c
        d["p2_tail10"] = {k: st.mean([c[k] for c in tail if c.get(k) is not None])
                          for k in ("dist_x_l", "dist_x_r", "height_fil", "height_raw",
                                    "roll_nav", "roll_ltg", "roll_hlr", "dd_fil", "hdg_fil")
                          if any(c.get(k) is not None for c in tail)}
    if p2 and d["p2_end"]:
        w0, w1 = p2[0], d["p2_end"]
        stopc = [c for c in p2c if w0 <= c["wall"] <= w1]
        if stopc:
            d["p2_stop_window"] = {
                "n": len(stopc),
                "dist_x_l": st.mean(c["dist_x_l"] for c in stopc if c.get("dist_x_l") is not None)
                if any(c.get("dist_x_l") is not None for c in stopc) else None,
                "dist_x_r": st.mean(c["dist_x_r"] for c in stopc if c.get("dist_x_r") is not None)
                if any(c.get("dist_x_r") is not None for c in stopc) else None,
                "height_fil": st.mean(c["height_fil"] for c in stopc if c.get("height_fil") is not None)
                if any(c.get("height_fil") is not None for c in stopc) else None,
                "roll_nav": st.mean(c["roll_nav"] for c in stopc if c.get("roll_nav") is not None)
                if any(c.get("roll_nav") is not None for c in stopc) else None,
                "roll_ltg": st.mean(c["roll_ltg"] for c in stopc if c.get("roll_ltg") is not None)
                if any(c.get("roll_ltg") is not None for c in stopc) else None,
                "steer": st.mean(c["steer"] for c in stopc if c.get("steer") is not None)
                if any(c.get("steer") is not None for c in stopc) else None,
            }
    d["notes"] = C.note_lines(test)
    d["manual"] = C.manual_stats(test)
    return d
