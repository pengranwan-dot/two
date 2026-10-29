#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把人工测量小节文件 <root>/stats/stat-<YYYYMMDD>.md 转成表格样式。

输入（小节格式，由人工手写）：
    ## 20261016_115052
    - 车头偏左 35
    - lift 15 14
    - 车头左右距离 141 152
    - 时间 100

输出（默认 <root>/stats/stat_table-<YYYYMMDD>.md）：
    测试 | 车头偏左 | 高度差-左 | 高度差-右 | 停车距离-左 | 停车距离-右 | 时间 | 人工roll(°) | 备注
可选再导出一份 CSV（--csv，便于贴到 Excel）。
可选附加环境温度列（--temp-topic temperature_env），按该话题全程均值填入。

也支持从 Excel 导出的 CSV 读入（--from-csv），表头可用中文或英文列名：
    测试,车头偏左,高度差-左,高度差-右,停车距离-左,停车距离-右,时间,人工roll(°),备注
    ts,head,lift_l,lift_r,dist_l,dist_r,time,roll,marker
（人工roll 列可省略，脚本会按 lift 左右重算。）

用法:
    python3 <tools>/stats_to_table.py --root data-1016
    python3 <tools>/stats_to_table.py --root data-1016 --date 20261016 --print
    python3 <tools>/stats_to_table.py --root data-1016 --csv stats/stat_table-20261016.csv
    python3 <tools>/stats_to_table.py --from-csv stats/stat-20261017.csv --out stats/stat_table-20261017.md
"""
import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

HEADER = ["测试", "车头偏左", "高度差-左", "高度差-右",
          "停车距离-左", "停车距离-右", "时间", "人工roll(°)", "备注"]
TEMP_HEADER = "环境温度(°C)"
TEMP_COL = 7          # 插在“时间”之后、“人工roll”之前


def stat_files(date=None):
    """返回 [(YYYYMMDD, Path)]；date 非空时只取该天。"""
    out = []
    if not C.STATS or not C.STATS.is_dir():
        return out
    for p in sorted(C.STATS.glob("stat-*.md")):
        name = p.stem[len("stat-"):]
        if name.startswith("stat_table"):          # 跳过本工具的输出
            continue
        if date and name != date:
            continue
        out.append((name, p))
    return out


def build_rows(parsed, temp_of=None):
    """parsed: {ts: {head, lift_l, lift_r, dist_l, dist_r, time, marker}} → 表格行。

    temp_of: 可选 callable(ts) → 环境温度（°C）或 None；提供时在“时间”列后插入温度列。"""
    rows = []
    for ts, m in parsed.items():
        temp = C.fmt(temp_of(ts), ".2f") if temp_of else None
        if "head" not in m and "lift_l" not in m:
            row = [ts, "--", "--", "--", "--", "--", "--", "--",
                   m.get("marker") or "未测量"]
            if temp_of:
                row.insert(TEMP_COL, temp)
            rows.append(row)
            continue
        roll = C.manual_roll_deg(m.get("lift_l"), m.get("lift_r"))
        row = [
            ts,
            C.fmt(m.get("head")),
            C.fmt(m.get("lift_l")), C.fmt(m.get("lift_r")),
            C.fmt(m.get("dist_l")), C.fmt(m.get("dist_r")),
            f"{m['time']:g}" if m.get("time") is not None else "-",
            f"{roll:+.3f}" if roll is not None else "--",
            m.get("marker") or "",
        ]
        if temp_of:
            row.insert(TEMP_COL, temp)
        rows.append(row)
    return rows


def header_for(temp_of):
    if not temp_of:
        return HEADER
    return HEADER[:TEMP_COL] + [TEMP_HEADER] + HEADER[TEMP_COL:]


def env_temp_func(topic):
    """返回 ts → 该次测量期间环境温度均值（°C）；无数据返回 None。"""
    tests = C.discover_tests()
    cache = {}

    def f(ts):
        if ts in cache:
            return cache[ts]
        test = tests.get(ts)
        val = None
        if test is not None:
            try:
                series = C.load_series(test, topic)
            except SystemExit:
                series = []
            vals = [v for _, v in series]
            if vals:
                val = sum(vals) / len(vals)
        cache[ts] = val
        return val
    return f


# CSV 表头别名：中文/英文/常见写法 → 内部字段
CSV_ALIASES = {
    "ts": "ts", "测试": "ts", "时间戳": "ts", "测试id": "ts",
    "head": "head", "车头偏左": "head", "偏左": "head", "横向偏差": "head",
    "lift_l": "lift_l", "高度差-左": "lift_l", "lift左": "lift_l", "高度差左": "lift_l",
    "lift_r": "lift_r", "高度差-右": "lift_r", "lift右": "lift_r", "高度差右": "lift_r",
    "dist_l": "dist_l", "停车距离-左": "dist_l", "距离左": "dist_l", "车头左右距离左": "dist_l",
    "dist_r": "dist_r", "停车距离-右": "dist_r", "距离右": "dist_r", "车头左右距离右": "dist_r",
    "time": "time", "时间": "time",
    "marker": "marker", "备注": "marker", "说明": "marker",
}


def _num(text):
    """空/'-'/'--' → None；否则转 int（失败再转 float）。"""
    s = (text or "").strip()
    if s in ("", "-", "--", "—"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def load_csv(path):
    """读 Excel 导出的 CSV → {ts: 测量 dict}（列名可用中文或英文）。"""
    out = C.OrderedDict()
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        rd = csv.reader(f)
        header = next(rd, None)
        if not header:
            return out
        cols = {}
        for i, h in enumerate(header):
            key = CSV_ALIASES.get(h.strip().lower()) or CSV_ALIASES.get(h.strip())
            if key and key != "roll":
                cols[key] = i
        if "ts" not in cols:
            raise SystemExit(f"{path} 缺少测试时间戳列（表头需含 测试/ts/时间戳）")
        for row in rd:
            if not row:
                continue
            def cell(k):
                i = cols.get(k)
                return row[i].strip() if i is not None and i < len(row) else ""
            ts = cell("ts")
            if not ts:
                continue
            d = {}
            for k in ("head", "lift_l", "lift_r", "dist_l", "dist_r"):
                v = _num(cell(k))
                if v is not None:
                    d[k] = v
            t = _num(cell("time"))
            if t is not None:
                d["time"] = float(t)
            mk = cell("marker")
            if mk:
                d["marker"] = mk
            out[ts] = d
    return out


def main():
    ap = argparse.ArgumentParser(description="人工测量（markdown 小节 或 CSV）→ 表格样式")
    ap.add_argument("--date", default=None, help="只转某天 YYYYMMDD（默认全部 stat-*.md）")
    ap.add_argument("--from-csv", default=None,
                    help="改从 CSV 读入（Excel 导出；表头可用中文或英文列名）")
    ap.add_argument("--out", default=None,
                    help="输出 markdown（默认 <root>/stats/stat_table-<date>.md；- 为终端）")
    ap.add_argument("--csv", default=None, help="同时导出 CSV 路径（可选）")
    ap.add_argument("--temp-topic", default=None,
                    help="附加环境温度列，取该话题（csv/bag）全程均值，如 temperature_env")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="只打印不写文件（等价 --out -）")
    ap.add_argument("--root", default=None, help="数据根目录（默认自动探测/DATA_ROOT）")
    args = ap.parse_args()

    C.init(args.root)
    temp_of = env_temp_func(args.temp_topic) if args.temp_topic else None
    header = header_for(temp_of)

    if args.from_csv:
        src = Path(args.from_csv)
        if not src.exists():
            sys.exit(f"CSV 不存在: {src}")
        parsed = load_csv(src)
        if not parsed:
            sys.exit(f"{src} 没有有效数据行")
        rows = build_rows(parsed, temp_of)
        m = re.search(r"(\d{8})", src.name)
        label = m.group(1) if m else src.stem
        blocks = [f"## {label}（n={len(rows)}）\n\n" + C.align_table(header, rows)]
        csv_rows = [["date", *header]] + [[label, *r] for r in rows]
        default = C.STATS / f"stat_table-{label}.md"
    else:
        files = stat_files(args.date)
        if not files:
            sys.exit(f"未找到 {C.STATS}/stat-*.md（--date {args.date}）")
        csv_rows, blocks = [], []
        for date, path in files:
            parsed = C.parse_stats_file(str(path))
            rows = build_rows(parsed, temp_of)
            if not rows:
                continue
            csv_rows += [["date", *header]]
            csv_rows += [[date, *r] for r in rows]
            blocks.append(f"## {date}（n={len(rows)}）\n\n" + C.align_table(header, rows))
        if not blocks:
            sys.exit("stats 文件里没有可转换的小节")
        default = C.STATS / f"stat_table-{files[0][0] if len(files) == 1 else 'all'}.md"

    text = "# 人工测量表\n\n" + "\n\n".join(blocks) + "\n"

    if args.csv:
        cp = Path(args.csv)
        cp.parent.mkdir(parents=True, exist_ok=True)
        with open(cp, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerows(csv_rows)
        print(f"已写入 {cp}")

    out = C.output_path(args.out, default, print_only=args.print_only)
    if out:
        C.write_text(out, text)
    else:
        print(text)


if __name__ == "__main__":
    main()
