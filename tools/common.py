#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据分析共享库（data_<YYYYMMDD> 按天数据目录）。

数据根目录（root，如 data_1013/）内含下列子目录；也兼容 root/data/ 嵌套一层：
    bags/autorecord_<YYYYMMDD_HHMMSS>/       rosbag2（*.db3 + metadata.yaml）
    logs/{motion_control_newdf,arc_detector_node,autorecord}_<ts>.log
    configs/autonomous_config[-_]<ts>/...    参数快照（两种前缀分隔符均支持）
    notes/note-<YYYYMMDD>.md                 试验备注（按时间戳行；兼容 note-<MMDD>.md）
    stats/stat-<YYYYMMDD>.md                 人工测量统计（按 `## <ts>` 小节；兼容 stat-<MMDD>.md）
    csv/<ts>/...                             extract_topics.py 导出的 CSV 缓存

root 解析顺序：--root 参数 > 环境变量 DATA_ROOT > 当前目录 / tools 上级目录 >
同级最新的 data_* 目录（多天共存时自动取最新，stderr 提示）。
提供：测试发现、控制器 log 解析、bag CDR 解码、人工测量解析、CJK 表格排版。
"""
import bisect
import functools
import glob
import math
import os
import re
import sqlite3
import sys
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

ROOT = DATA = BAGS = LOGS = CONFIGS = NOTES = STATS = CSV = None


_DAY_NAME_RE = re.compile(r"^data(?:[-_]|$)")


def _looks_like_root(c):
    return (c / "data" / "bags").is_dir() or (c / "bags").is_dir()


def _day_sort_key(p):
    m = re.search(r"(\d+)\D*$", p.name)
    return (int(m.group(1)) if m else -1, p.name)


_AUTO_NOTICE = None


def _autodetect_root():
    global _AUTO_NOTICE
    here = Path(__file__).resolve().parent
    cwd = Path.cwd()
    for c in (cwd, here, here.parent):
        if _looks_like_root(c) and ((c / "bags").is_dir() or _DAY_NAME_RE.match(c.name)):
            return c
    cands = []
    for base in dict.fromkeys((cwd, here.parent)):
        for pat in ("data_*", "data-*"):
            for c in base.glob(pat):
                if _looks_like_root(c) and c not in cands:
                    cands.append(c)
    cands.sort(key=_day_sort_key, reverse=True)
    if not cands:
        raise SystemExit("未找到数据目录：请用 --root <data_YYYYMMDD> 指定，或设置环境变量 DATA_ROOT")
    if len(cands) > 1:
        _AUTO_NOTICE = f"[info] 自动选择最新数据目录：{cands[0]}（可用 --root 指定其它）"
    return cands[0]


def init(root=None, quiet=False):
    """解析数据根目录并初始化全局路径；返回 root。

    无显式 root/环境变量时沿用已初始化的目录（避免重复探测）。
    quiet=True（模块导入时）不打印自动选择提示，脚本 main 中调用时打印一次。"""
    global ROOT, DATA, BAGS, LOGS, CONFIGS, NOTES, STATS, CSV, _AUTO_NOTICE
    root = root or os.environ.get("DATA_ROOT")
    if not root:
        if ROOT is not None:
            if _AUTO_NOTICE and not quiet:
                print(_AUTO_NOTICE, file=sys.stderr)
                _AUTO_NOTICE = None
            return ROOT
        root = _autodetect_root()
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"数据根目录不存在: {root}")
    ROOT = root
    DATA = root / "data" if (root / "data" / "bags").is_dir() else root
    BAGS = DATA / "bags"
    LOGS = DATA / "logs"
    CONFIGS = DATA / "configs"
    NOTES = DATA / "notes"
    STATS = DATA / "stats"
    CSV = ROOT / "csv"
    if _AUTO_NOTICE and not quiet:
        print(_AUTO_NOTICE, file=sys.stderr)
        _AUTO_NOTICE = None
    return root


try:
    init(quiet=True)
except SystemExit:
    pass

LOG_NODES = ("motion_control_newdf", "arc_detector_node", "autorecord")
TS_RE = re.compile(r"(\d{8})_(\d{6})")
LINE_RE = re.compile(r"^\[([IWEC])\]\s+\[(\d\d):(\d\d):(\d\d)\.(\d+)\]\s+(.*)$")
CYCLE_RE = re.compile(r"t=\s*([\d.]+)s\s+\[(\w+)\]")
STAGE_RE = re.compile(r"STAGE\s+(\w+)\s+->\s+(\w+)")
PHASE2_STOP_RE = re.compile(r"PHASE2-STOP\s+(active|done)(?::\s*(.*))?")
PHASE1_STOP_RE = re.compile(r"PHASE1-STOP\s+(active|done)(?::\s*(.*))?")
STARTUP_ACTIVE_RE = re.compile(
    r"STARTUP strategy active:\s*y_R=([+-][\d.]+)\s+hdg=([+-][\d.]+)°"
    r"\s+d_head=([+-][\d.]+)\s+d_tail=([+-][\d.]+)\s+d_c=([+-][\d.]+)"
    r"\s*->\s*(\w+)\s*\((\d+)\s*samples mean\)")
STARTUP_DONE_RE = re.compile(
    r"STARTUP strategy done:\s*y_R=([+-][\d.]+)\s+hdg=([+-][\d.]+)°\s*->\s*(\w+)")
CONVERGED_RE = re.compile(r"CONVERGED\s*->\s*publish\s*/control/stop")


@dataclass
class Test:
    ts: str
    bag: Path = None
    motion_log: Path = None
    arc_log: Path = None
    recorder_log: Path = None
    config: Path = None
    note: Path = None
    stats: Path = None

    @property
    def date(self):
        return self.ts.split("_")[0]

    @property
    def clock(self):
        return self.ts.split("_")[1]

    def files_ok(self):
        return bool(self.bag and self.motion_log)


def ts_of(name):
    m = TS_RE.search(name)
    return f"{m.group(1)}_{m.group(2)}" if m else None


def discover_tests():
    tests = {}
    if BAGS.is_dir():
        for p in sorted(BAGS.iterdir()):
            ts = ts_of(p.name)
            if ts and p.is_dir():
                tests.setdefault(ts, Test(ts)).bag = p
    if LOGS.is_dir():
        for p in sorted(LOGS.iterdir()):
            if not p.name.endswith(".log"):
                continue
            ts = ts_of(p.name)
            if not ts:
                continue
            t = tests.setdefault(ts, Test(ts))
            for node in LOG_NODES:
                if p.name.startswith(node + "_"):
                    setattr(t, {"motion_control_newdf": "motion_log",
                                "arc_detector_node": "arc_log",
                                "autorecord": "recorder_log"}[node], p)
    if CONFIGS.is_dir():
        snaps = sorted((p for p in CONFIGS.iterdir() if p.is_dir()
                        and ts_of(p.name)), key=lambda p: ts_of(p.name))
        for t in tests.values():
            cand = [p for p in snaps if ts_of(p.name) <= t.ts]
            t.config = cand[-1] if cand else (snaps[0] if snaps else None)
    if NOTES.is_dir():
        for t in tests.values():
            p = dated_file(NOTES, "note", t.date)
            if p:
                t.note = p
    if STATS.is_dir():
        for t in tests.values():
            p = dated_file(STATS, "stat", t.date)
            if p:
                t.stats = p
    return OrderedDict(sorted(tests.items()))


def dated_file(directory, prefix, date):
    """按 `prefix-<YYYYMMDD>.md` 查找；兼容旧命名 `prefix-<MMDD>.md`。"""
    for name in (f"{prefix}-{date}.md", f"{prefix}-{date[4:]}.md"):
        p = directory / name
        if p.exists():
            return p
    return None


def resolve_test(arg, tests=None):
    tests = tests or discover_tests()
    if arg in tests:
        return tests[arg]
    cands = [t for ts, t in tests.items() if arg in ts]
    if len(cands) == 1:
        return cands[0]
    p = Path(arg)
    if p.is_dir():
        ts = ts_of(p.name) or (p.parent.name if ts_of(p.parent.name) else None)
        if ts and ts in tests:
            return tests[ts]
    raise SystemExit(f"无法定位测试: {arg}（可用 id 时间戳或唯一子串，见 list_tests.py）")


def read_lines(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def output_path(out, default, print_only=False):
    """解析输出目标：print_only 或 out=="-" → None（stdout）；out 为空 → default；否则 out。"""
    if print_only or out == "-":
        return None
    return Path(out) if out else Path(default)


def write_text(path, text):
    """写文件（自动建目录）并打印提示。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"已写入 {path}")


def display_width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def align_table(header, rows):
    rows = [[str(c) for c in r] for r in rows]
    header = [str(c) for c in header]
    widths = [max(display_width(r[j]) for r in [header] + rows) for j in range(len(header))]
    out = []
    for k, r in enumerate([header] + rows):
        out.append("| " + " | ".join(
            c + " " * (widths[j] - display_width(c)) for j, c in enumerate(r)) + " |")
        if k == 0:
            out.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    return "\n".join(out)


def fmt(v, spec="", dash="--"):
    if v is None:
        return dash
    return format(v, spec) if spec else str(v)


def wall_seconds(h, m, s, frac):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(frac) / (10 ** len(frac))


def hms(sec):
    if sec is None:
        return "--"
    sec %= 86400
    return f"{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{sec % 60:06.3f}"


def parse_clock(text):
    m = re.match(r"(\d\d):(\d\d):(\d\d)(?:\.(\d+))?", text)
    if not m:
        return None
    return wall_seconds(m.group(1), m.group(2), m.group(3), m.group(4) or "0")


def note_lines(test):
    """返回 note 文件中与测试时间戳对应的备注行（去除时间戳前缀）。"""
    if not test.note or not test.note.exists():
        return []
    out = []
    for ln in read_lines(test.note):
        ln = ln.strip()
        if ln.startswith(test.ts):
            out.append(ln[len(test.ts):].strip(" :，,、-"))
    return out


def note_global_lines(test):
    if not test.note or not test.note.exists():
        return []
    return [ln.strip() for ln in read_lines(test.note)
            if ln.strip() and not re.match(r"^\d{8}_\d{6}\b", ln.strip())]


# ---------------------------------------------------------------- stats parsing

@functools.lru_cache(maxsize=16)
def parse_stats_file(path):
    """解析人工测量统计文件 <root>/stats/stat-<YYYYMMDD>.md。

    小节格式（同旧 README）：
        ## 20261012_154647
        - 车头偏左 -13
        - lift 10 2
        - 车头左右距离 132 144
        - 时间 100

    返回 {ts: {head, lift_l, lift_r, dist_l, dist_r, time, marker}}；
    无有效测量的小节（如 “有问题”）只有 marker 字段。"""
    out = OrderedDict()
    path = Path(path)
    if not path.exists():
        return out
    text = path.read_text(encoding="utf-8", errors="replace")
    for sec in re.split(r"^##\s+", text, flags=re.M)[1:]:
        lines = sec.rstrip().splitlines()
        if not lines:
            continue
        ts = lines[0].strip()
        if not TS_RE.fullmatch(ts):
            continue
        body = "\n".join(lines[1:])
        d = {}
        m = re.search(r"车头偏左\s*(-?\d+)", body)
        if m:
            d["head"] = int(m.group(1))
        m = re.search(r"lift\s*(-?\d+)\s+(-?\d+)", body)
        if m:
            d["lift_l"], d["lift_r"] = int(m.group(1)), int(m.group(2))
        m = re.search(r"车头左右距离\s*(-?\d+)\s+(-?\d+)", body)
        if m:
            d["dist_l"], d["dist_r"] = int(m.group(1)), int(m.group(2))
        m = re.search(r"时间\s*([\d.]+)", body)
        if m:
            d["time"] = float(m.group(1))
        for ln in lines[1:]:
            s = ln.strip()
            if s and not s.startswith("-"):
                d["marker"] = s.rstrip("：:")
                break
        out[ts] = d
    return out


def manual_stats(test):
    """某测试的人工测量（无 stats 文件/无该小节返回 None）。"""
    if not test.stats:
        return None
    return parse_stats_file(str(test.stats)).get(test.ts)


def manual_roll_deg(lift_l, lift_r, span=1.6):
    """人工左右高度差(mm) → roll(deg)：atan2((lift_l − lift_r)/1000, span)。"""
    if lift_l is None or lift_r is None:
        return None
    return math.degrees(math.atan2((lift_l - lift_r) / 1000.0, span))


# ---------------------------------------------------------------- log parsing

def _f(m, g=1):
    return float(m.group(g)) if m else None


def parse_cycle(line):
    m = CYCLE_RE.search(line)
    if not m:
        return None
    d = {"t": float(m.group(1)), "stage": m.group(2)}
    d["target_R"] = None
    m2 = re.search(r"target_R=\(\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)", line)
    if m2:
        d["target_R"] = tuple(float(x) for x in m2.groups())
    d["yaw_err"] = _f(re.search(r"yaw_err=([+-]?[\d.]+)°", line))
    d["lift"] = _f(re.search(r"lift=([+-]?[\d.]+)%", line))
    m2 = re.search(r"lift=[+-][\d.]+%\s*\(fin ([+-][\d.]+), P ([+-][\d.]+), I ([+-][\d.]+), D ([+-][\d.]+)\)", line)
    d["lift_fin"] = _f(m2, 1) if m2 else None
    m2 = re.search(r"roll=\(tgt ([+-][\d.]+)°, nav ([+-][\d.]+)°, cmd ([+-][\d.]+), hlr ([+-][\d.]+)°, topline ([+-][\d.]+)°, ncomp ([+-][\d.]+)°, ff ([+-][\d.]+)(?:°)?, ltg ([+-][\d.]+)°\)", line)
    if m2:
        (d["roll_tgt"], d["roll_nav"], d["roll_cmd"], d["roll_hlr"],
         d["roll_topline"], d["roll_ncomp"], d["roll_ff"], d["roll_ltg"]) = \
            (float(x) for x in m2.groups())
    else:
        m2 = re.search(r"roll=\(tgt ([+-][\d.]+)°, nav ([+-][\d.]+)°, cmd ([+-][\d.]+), hlr ([+-][\d.]+)°, topline ([+-][\d.]+)°, ncomp ([+-][\d.]+)°, ff ([+-][\d.]+)°?\)", line)
        if m2:
            (d["roll_tgt"], d["roll_nav"], d["roll_cmd"], d["roll_hlr"],
             d["roll_topline"], d["roll_ncomp"], d["roll_ff"]) = \
                (float(x) for x in m2.groups())
            d["roll_ltg"] = None
    d["roll_fin"] = _f(re.search(r"roll=\([^)]*\)\s*\(fin ([+-][\d.]+), P ([+-][\d.]+), I ([+-][\d.]+), D ([+-][\d.]+)\)", line))
    d["steer"] = _f(re.search(r"steer=([+-]?[\d.]+)°", line))
    d["thr"] = _f(re.search(r"thr=([\d.]+)%", line))
    d["v"] = _f(re.search(r"v=([+-]?[\d.]+)m/s", line))
    m2 = re.search(r"mode=(\w+)", line)
    d["mode"] = m2.group(1) if m2 else None
    d["oil_temp"] = _f(re.search(r"oil_temp=([+-][\d.]+)°C", line))
    d["temp"] = _f(re.search(r"(?<![_\w])temp=([+-][\d.]+)°C", line))
    d["ext"] = _f(re.search(r"ext=([+-]?\d+)", line))
    d["thermal"] = {}
    m2 = re.search(r"thermal=\[([^\]]*)\]", line)
    if m2:
        for kv in m2.group(1).split(","):
            k, _, v = kv.partition(":")
            try:
                d["thermal"][k.strip()] = float(v)
            except ValueError:
                pass
    if d["stage"] == "2" or "[2]" in line:
        m2 = re.search(r"dd=\(raw ([+-][\d.]+), fil ([+-][\d.]+)\)", line)
        if m2:
            d["dd_raw"], d["dd_fil"] = float(m2.group(1)), float(m2.group(2))
        else:
            v = _f(re.search(r"distance_diff=([+-][\d.]+)", line))
            d["dd_raw"] = d["dd_fil"] = v
        d["ps"] = _f(re.search(r"\bps=(-?\d+)", line))
        m2 = re.search(r"rect_y=\(raw ([+-][\d.]+), fil ([+-][\d.]+)\)", line)
        if m2:
            d["rect_raw"], d["rect_fil"] = float(m2.group(1)), float(m2.group(2))
        m2 = re.search(r"dist_x=\(([+-][\d.]+),([+-][\d.]+)\)", line)
        if m2:
            d["dist_x_l"], d["dist_x_r"] = float(m2.group(1)), float(m2.group(2))
        d["dist_travel"] = _f(re.search(r"dist_travel=([\d.]+)m", line))
        m2 = re.search(r"hdg=\(raw ([+-][\d.]+)°, fil ([+-][\d.]+)°\)", line)
        if m2:
            d["hdg_raw"], d["hdg_fil"] = float(m2.group(1)), float(m2.group(2))
        m2 = re.search(r"dxdiff=\(raw ([+-][\d.]+), fil ([+-][\d.]+)\)", line)
        if m2:
            d["dxdiff_raw"], d["dxdiff_fil"] = float(m2.group(1)), float(m2.group(2))
        m2 = re.search(r"height=\(target ([\d.]+)m, hfil ([+-]?[\d.]+)m, raw ([+-]?[\d.]+)m, "
                       r"lift cmd ([+-][\d.]+)%, hcomp ([+-][\d.]+)m, hfit ([+-]?[\d.]+)m\)", line)
        if m2:
            (d["height_target"], d["height_fil"], d["height_raw"],
             d["height_cmd"], d["height_comp"], d["height_fit"]) = \
                (float(x) for x in m2.groups())
        m2 = re.search(r"gh=\(([+-][\d.]+),([+-][\d.]+),([+-][\d.]+)\)", line)
        if m2:
            d["gh"] = tuple(float(x) for x in m2.groups())
        m2 = re.search(r"source=\((\d+),(\d+)\)", line)
        if m2:
            d["source"] = (int(m2.group(1)), int(m2.group(2)))
    return d


def iter_log(path):
    """逐行解析控制器 log，产出 (kind, wall_sec, dict)。

    kind: cycle / stage / startup / phase2_stop / phase1_stop / converged / note(其他)
    """
    if not path or not Path(path).exists():
        return
    for line in read_lines(path):
        m = LINE_RE.match(line)
        if not m:
            continue
        level, h, mi, s, frac, body = m.groups()
        wall = wall_seconds(h, mi, s, frac)
        cyc = parse_cycle(body)
        if cyc:
            cyc["wall"] = wall
            yield "cycle", wall, cyc
            continue
        m2 = STAGE_RE.search(body)
        if m2:
            extra = {}
            mx = re.search(r"x_R=([-\d.]+)\s+y_R=([-\d.]+)\s+z_R=([-\d.]+)\s+hdg=([+-][\d.]+)°", body)
            if mx:
                extra = {"x_R": float(mx.group(1)), "y_R": float(mx.group(2)),
                         "z_R": float(mx.group(3)), "hdg": float(mx.group(4))}
            extra["text"] = body
            yield "stage", wall, dict(extra, frm=m2.group(1), to=m2.group(2))
            continue
        if "STARTUP" in body:
            m2 = STARTUP_ACTIVE_RE.search(body)
            if m2:
                yield "startup_active", wall, {
                    "y_R": float(m2.group(1)), "hdg": float(m2.group(2)),
                    "d_head": float(m2.group(3)), "d_tail": float(m2.group(4)),
                    "d_c": float(m2.group(5)), "mode": m2.group(6),
                    "samples": int(m2.group(7)), "text": body}
                continue
            m2 = STARTUP_DONE_RE.search(body)
            if m2:
                yield "startup_done", wall, {
                    "y_R": float(m2.group(1)), "hdg": float(m2.group(2)),
                    "mode": m2.group(3), "text": body}
                continue
            yield "startup", wall, {"text": body}
            continue
        m2 = PHASE2_STOP_RE.search(body)
        if m2:
            yield "phase2_stop", wall, {"action": m2.group(1),
                                        "detail": (m2.group(2) or "").strip(), "text": body}
            continue
        m2 = PHASE1_STOP_RE.search(body)
        if m2:
            yield "phase1_stop", wall, {"action": m2.group(1),
                                        "detail": (m2.group(2) or "").strip(), "text": body}
            continue
        if CONVERGED_RE.search(body):
            yield "converged", wall, {"text": body}
            continue
        yield "note", wall, {"level": level, "text": body}


def log_events(path):
    """收集 log 中的事件列表（非周期行）。"""
    return [(k, w, d) for k, w, d in iter_log(path) if k != "cycle"]


def parse_cycles(path):
    return [d for k, w, d in iter_log(path) if k == "cycle"]


# ---------------------------------------------------------------- bag access

def bag_db(bag):
    dbs = sorted(glob.glob(os.path.join(str(bag), "*.db3")))
    return dbs


def bag_connect(bag):
    dbs = bag_db(bag)
    if not dbs:
        return None
    return sqlite3.connect(f"file:{dbs[0]}?mode=ro", uri=True)


def bag_topics(bag):
    con = bag_connect(bag)
    if con is None:
        return []
    try:
        out = []
        for tid, name, typ in con.execute("select id,name,type from topics order by id"):
            n = con.execute("select count(*) from messages where topic_id=?", (tid,)).fetchone()[0]
            out.append((name, typ, n))
        return out
    finally:
        con.close()


def bag_duration(bag):
    con = bag_connect(bag)
    if con is None:
        return None, None, None
    try:
        mn, mx, n = con.execute("select min(timestamp),max(timestamp),count(*) from messages").fetchone()
        if mn is None:
            return None, None, 0
        return mn / 1e9, mx / 1e9, n
    finally:
        con.close()


def bag_series(bag, topic):
    """读 bag 话题 → [(epoch, raw_bytes)]。"""
    con = bag_connect(bag)
    if con is None:
        return []
    try:
        row = con.execute("select id from topics where name=?", (topic,)).fetchone()
        if not row:
            return []
        return [(ts / 1e9, data) for ts, data in
                con.execute("select timestamp,data from messages where topic_id=?", (row[0],))]
    finally:
        con.close()


def cd_f64(data):
    return struct_unpack("<d", data, 4)


def cd_f32(data):
    return struct_unpack("<f", data, 4)


def struct_unpack(fmt, data, off):
    import struct
    return struct.unpack_from(fmt, data, off)[0]


def cd_point(data):
    import struct
    return struct.unpack_from("<3d", data, 4)


def cd_vector3(data):
    return cd_point(data)


def cd_quat(data):
    import struct
    return struct.unpack_from("<4d", data, 4)


def cd_bool(data):
    return bool(data[4])


def cd_int8(data):
    import struct
    return struct.unpack_from("<b", data, 4)[0]


def cd_temp(data):
    """sensor_msgs/Temperature：header(stamp+frame_id) 对齐后取 double 温度。"""
    import struct
    pos = 8
    (slen,) = struct.unpack_from("<I", data, 4 + pos)
    pos += 4 + slen
    pos = (pos + 7) // 8 * 8
    return struct.unpack_from("<d", data, 4 + pos)[0]


def _cdr_align(pos, n):
    return (pos + n - 1) // n * n


def cd_hcinspvatzcb_roll(data):
    """Hcinspvatzcb.msg 中 roll（float32, deg）。"""
    pos = 8
    import struct
    (slen,) = struct.unpack_from("<I", data, 4 + pos)
    pos += 4 + slen
    pos = _cdr_align(pos, 2) + 2
    pos = _cdr_align(pos, 8) + 8
    pos += 16 + 4 + 12 + 4
    pos = _cdr_align(pos, 4)
    return struct_unpack("<f", data, 4 + pos)


DECODERS = {
    "std_msgs/msg/Float64": ("value", cd_f64),
    "std_msgs/msg/Bool": ("value", cd_bool),
    "std_msgs/msg/Int8": ("value", cd_int8),
    "sensor_msgs/msg/Temperature": ("temperature", cd_temp),
    "sensor_msgs/msg/RelativeHumidity": ("relative_humidity", cd_temp),
    "geometry_msgs/msg/Point": (("x", "y", "z"), cd_point),
    "geometry_msgs/msg/Vector3": (("x", "y", "z"), cd_vector3),
    "geometry_msgs/msg/Quaternion": (("x", "y", "z", "w"), cd_quat),
    "msg_interfaces/msg/Hcinspvatzcb": ("roll", cd_hcinspvatzcb_roll),
}


def norm_topic(topic):
    """统一话题名形态（bag 中均为 /xxx）；用户可传不带前导斜杠的短名。"""
    return "/" + str(topic).strip("/")


def load_series(test, topic):
    """优先 csv 缓存（csv/<ts>/<topic>.csv），缺失回退 bag。

    topic 可带或不带前导斜杠（“temperature_env” 与 “/temperature_env” 等价）。
    返回 [(epoch, value)] 或 [(epoch, (x,y,z...))]；无数据返回 []。"""
    topic = norm_topic(topic)
    p = CSV / test.ts / (topic.lstrip("/") + ".csv")
    if p.exists():
        try:
            return _read_csv_series(p)
        except OSError:
            pass
    if not test.bag:
        return []
    name, dec = _decoder_for(test, topic)
    out = []
    for ts, data in bag_series(test.bag, topic):
        try:
            v = dec(data)
        except Exception:
            continue
        if not isinstance(name, str):
            out.append((ts, tuple(v)))
        else:
            out.append((ts, float(v)))
    return out


def _decoder_for(test, topic):
    topic = norm_topic(topic)
    names = []
    for name, typ, n in bag_topics(test.bag):
        names.append(name)
        if name == topic:
            if typ in DECODERS:
                return DECODERS[typ]
            raise SystemExit(
                f"话题 {topic} 类型为 {typ}，暂无解码器（{test.ts}）")
    hint = [n for n in names if topic.strip("/") in n.strip("/")]
    msg = f"话题 {topic} 不存在（{test.ts}）"
    if hint:
        msg += "；相近话题：" + ", ".join(hint)
    raise SystemExit(msg)


def _read_csv_series(p):
    import csv as _csv
    out = []
    with open(p, newline="", encoding="utf-8", errors="replace") as f:
        rd = _csv.reader(f)
        header = next(rd, None)
        if not header or header[0] != "time":
            return out
        ncol = len(header) - 1
        for row in rd:
            if len(row) < 1 + ncol:
                continue
            try:
                t = float(row[0])
                vals = tuple(float(x) for x in row[1:1 + ncol])
            except ValueError:
                continue
            out.append((t, vals[0] if ncol == 1 else vals))
    return out


def mean_window(series, t0, t1):
    vals = [v for t, v in series if t0 <= t < t1]
    if not vals:
        return None, 0
    if isinstance(vals[0], tuple):
        n = len(vals[0])
        return tuple(sum(v[i] for v in vals) / len(vals) for i in range(n)), len(vals)
    return sum(vals) / len(vals), len(vals)


def interp_series(series):
    ts = [t for t, _ in series]
    vs = [v for _, v in series]

    def f(t):
        i = bisect.bisect_left(ts, t)
        return vs[min(max(i, 0), len(vs) - 1)]
    return f


# ---------------------------------------------------------------- startup geometry

def rotation_between(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-12 or nb < 1e-12:
        return None
    a = [x / na for x in a]
    b = [x / nb for x in b]
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    v = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    s = math.sqrt(sum(x * x for x in v))
    if s < 1e-12:
        return [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]] if dot > 0 else \
               [[-1.0, 0, 0], [0, -1.0, 0], [0, 0, 1.0]]
    K = [[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]]
    c = dot
    f = (1 - c) / (s * s)
    R = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            R[i][j] = (1.0 if i == j else 0.0) + K[i][j] + \
                sum(K[i][k] * K[k][j] for k in range(3)) * f
    return R


def mat_vec(R, v):
    return [sum(R[i][j] * v[j] for j in range(3)) for i in range(3)]


def rectifying_rotation(a, b, c):
    return rotation_between([a, b, c], [0.0, 0.0, 1.0])


def initial_deviations(test, head_t=None, src=1, tail=8.5):
    """从 bag 感知复算 STARTUP 初始 y_R/hdg/d_head/d_tail（控制器同公式）。

    src: 1=top_line_dir（默认），0=arch_normal。返回 [dict]，或 []。"""
    if not test.bag:
        return []
    needed = ["/arc_detector/target_point", "/arc_detector/ground_plane",
              "/arc_detector/top_line_dir", "/arc_detector/arch_normal"]
    have = {n for n, _, c in bag_topics(test.bag) if c}
    if any(n not in have for n in needed):
        return []
    tg = load_series(test, needed[0])
    gp = load_series(test, needed[1])
    tl = load_series(test, needed[2])
    an = load_series(test, needed[3])
    if not (tg and gp and tl and an):
        return []
    fgp, ftl, fan = interp_series(gp), interp_series(tl), interp_series(an)
    t0 = max(tg[0][0], gp[0][0], tl[0][0], an[0][0])
    rows = []
    for t, p in tg:
        if t < t0 or (head_t is not None and t > t0 + head_t):
            continue
        gp = fgp(t)
        R = rectifying_rotation(gp[0], gp[1], gp[2])
        if R is None:
            continue
        P = mat_vec(R, list(p))
        x_R, y_R = P[0], P[1]
        if src == 0:
            n_R = mat_vec(R, list(fan(t)))
            hdg = math.degrees(math.atan2(-n_R[1], -n_R[0]))
            nx, ny = n_R[0], n_R[1]
        else:
            d_R = mat_vec(R, list(ftl(t)))
            hdg = math.degrees(math.atan2(-d_R[0], d_R[1]))
            nx, ny = -d_R[1], d_R[0]
        norm = math.hypot(nx, ny)
        if norm < 1e-9:
            continue
        nx, ny = nx / norm, ny / norm
        rows.append({"t": t, "y_R": y_R, "hdg": hdg,
                     "d_head": -nx * y_R + ny * x_R,
                     "d_tail": -nx * y_R + ny * (tail + x_R)})
    return rows


def wall_to_epoch(test, wall):
    """log 墙钟秒 → epoch 秒（以 bag 起始时刻所在日期为基准）。"""
    if not test.bag:
        return None
    t0, _, _ = bag_duration(test.bag)
    if t0 is None:
        return None
    import datetime
    d = datetime.datetime.fromtimestamp(t0)
    epoch = datetime.datetime(d.year, d.month, d.day).timestamp() + wall
    if epoch < t0 - 6 * 3600:
        epoch += 86400
    elif epoch > t0 + 18 * 3600:
        epoch -= 86400
    return epoch


def yaml_value(path, key):
    if not path or not Path(path).exists():
        return None
    pat = re.compile(r"^\s*" + re.escape(key) + r":\s*([^#\n]+?)\s*(?:#.*)?$", re.M)
    m = pat.search(Path(path).read_text(encoding="utf-8", errors="replace"))
    return m.group(1).strip() if m else None


def config_yaml(test, name="configs/newdf_dpt15/control/control_config.yaml"):
    if not test.config:
        return None
    return Path(test.config) / name
