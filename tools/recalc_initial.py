#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 bag 感知数据复算控制器 STARTUP 初始 y_R / hdg / d_head / d_tail。

公式与 parking_auto_controller.hpp 一致（src=1 用 top_line_dir，src=0 用 arch_normal）：
    R_R_L = rectifying_rotation(ground_plane)
    hdg   = atan2(-d_R.x, d_R.y)
    d_head = -n̂x·y_R + n̂y·x_R，d_tail = -n̂x·y_R + n̂y·(L + x_R)

用法:
    python3 <tools>/recalc_initial.py 154647
    python3 <tools>/recalc_initial.py 154647 --src 0 --tail 8.5
    python3 <tools>/recalc_initial.py 183808 --time 3     # 只取前 3s 样本
"""
import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C


def main():
    ap = argparse.ArgumentParser(description="复算初始偏差（bag 感知）")
    ap.add_argument("test", help="测试时间戳（如 154647）")
    ap.add_argument("--src", type=int, default=1, help="heading source: 1=top_line_dir（默认）0=arch_normal")
    ap.add_argument("--tail", type=float, default=8.5, help="车尾距离 L")
    ap.add_argument("--time", type=float, default=None, help="只取触发后前 T 秒样本")
    ap.add_argument("--root", default=None,
                    help="数据根目录（如 data_1013；默认环境变量 DATA_ROOT 或自动探测）")

    args = ap.parse_args()

    C.init(args.root)

    t = C.resolve_test(args.test)
    rows = C.initial_deviations(t, head_t=args.time, src=args.src, tail=args.tail)
    if not rows:
        sys.exit(f"{t.ts} 无有效感知样本（缺话题或 bag）")
    first = rows[:5]
    print(f"测试: {t.ts}  样本数: {len(rows)}  （src={args.src}, L={args.tail}）")
    print(f"首个有效窗口均值: y_R={st.mean(r['y_R'] for r in first):+.3f}  "
          f"hdg={st.mean(r['hdg'] for r in first):+.3f}°  "
          f"d_head={st.mean(r['d_head'] for r in first):+.3f}  "
          f"d_tail={st.mean(r['d_tail'] for r in first):+.3f}")
    print("前 10 个样本:")
    for r in rows[:10]:
        print(f"  t={r['t']:.2f}  y_R={r['y_R']:+.3f}  hdg={r['hdg']:+.3f}°  "
              f"d_head={r['d_head']:+.3f}  d_tail={r['d_tail']:+.3f}")


if __name__ == "__main__":
    main()
