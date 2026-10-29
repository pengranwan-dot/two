#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比 <root>/configs 下的参数快照，输出各快照相对上一快照的变化。

快照目录：<root>/configs/autonomous_config[-_]<YYYYMMDD_HHMMSS>/
对比文件：configs/newdf_dpt15/control/control_config.yaml（--file 可换）

用法:
    python3 <tools>/config_diff.py                     # 全部快照顺序对比
    python3 <tools>/config_diff.py --from 20261012_150000
    python3 <tools>/config_diff.py --baseline /path/yaml --out /tmp/diff.md
    python3 <tools>/config_diff.py --assoc             # 附测试→快照映射
    python3 <tools>/config_diff.py --nest              # auto 段参数归入 auto:
"""
import argparse
import re
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C

DEFAULT_FILE = "configs/newdf_dpt15/control/control_config.yaml"


def snapshots():
    if not C.CONFIGS.is_dir():
        return []
    out = []
    for p in sorted(C.CONFIGS.iterdir(), key=lambda x: C.ts_of(x.name) or ""):
        ts = C.ts_of(p.name)
        if p.is_dir() and ts:
            out.append((ts, p))
    return out


def parse_yaml_params(path):
    params = OrderedDict()
    section = ""
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([A-Za-z_][\w]*):(.*)$", line)
        if not m:
            continue
        indent, key, rest = len(m.group(1)), m.group(2), m.group(3)
        val = rest.split("#", 1)[0].strip()
        if val == "":
            if indent == 2 and key == "auto":
                section = "auto"
            elif indent == 0:
                section = ""
            continue
        params[key] = (section, val)
    return params


def diff_params(cur, prev):
    out = OrderedDict()
    for key, (section, val) in cur.items():
        old = prev.get(key, (section, None))[1]
        if old != val:
            out[key] = (section, old, val)
    for key, (section, val) in prev.items():
        if key not in cur:
            out[key] = (section, val, "（已删除）")
    return out


def render_changes(changes, nest=False):
    flat, nested = [], []
    for key, (section, old, new) in changes.items():
        line = f"{key}: {new} # {old if old is not None else '新增'}"
        if nest and section == "auto":
            nested.append(line)
        else:
            flat.append(line)
    body = list(flat)
    if nested:
        body.append("auto:")
        body += ["    " + ln for ln in nested]
    return "\n".join(body)


def main():
    ap = argparse.ArgumentParser(description="config 快照对比")
    ap.add_argument("--file", default=DEFAULT_FILE, help=f"对比文件（默认 {DEFAULT_FILE}）")
    ap.add_argument("--baseline", default=None, help="首个快照基线（快照目录或 yaml 路径）")
    ap.add_argument("--from", dest="from_ts", default=None, help="只对比该时间戳及之后的快照")
    ap.add_argument("--nest", action="store_true", help="auto 段参数归入 auto:")
    ap.add_argument("--assoc", action="store_true", help="附各测试→生效快照映射")
    ap.add_argument("--out", default=None,
                    help="输出文件（默认 <root>/reports/config_changes.md；- 为打印到终端）")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="只打印不写文件（等价 --out -）")
    ap.add_argument("--root", default=None,
                    help="数据根目录（如 data_1013；默认环境变量 DATA_ROOT 或自动探测）")

    args = ap.parse_args()

    C.init(args.root)

    snaps = snapshots()
    if args.from_ts:
        snaps = [s for s in snaps if s[0] >= args.from_ts]
    if not snaps:
        sys.exit("未找到 config 快照")

    base_params = base_name = None
    if args.baseline:
        p = Path(args.baseline)
        if p.is_dir():
            p = p / args.file
        if p.exists():
            base_params, base_name = parse_yaml_params(p), p.parent.name
        else:
            sys.exit(f"基线不存在: {args.baseline}")

    lines = ["# config 快照参数变化（<root>/configs）", ""]
    prev_params, prev_label = base_params, base_name
    for ts, snap in snaps:
        path = snap / args.file
        if not path.exists():
            lines += [f"## {ts}", "", f"（缺 {args.file}）", ""]
            continue
        params = parse_yaml_params(path)
        if prev_params is None:
            lines += [f"## {ts}", "", "（首个快照，无基线可比；可用 --baseline 指定）", ""]
            prev_params, prev_label = params, ts
            continue
        changes = diff_params(params, prev_params)
        lines += [f"## {ts}（相对 {prev_label}）", ""]
        if changes:
            lines += ["```yaml", render_changes(changes, nest=args.nest), "```", ""]
        else:
            lines += ["（无参数变化）", ""]
        prev_params, prev_label = params, ts

    if args.assoc:
        lines += ["## 测试 → 生效快照", ""]
        rows = [[ts, t.config.name.replace("autonomous_config-", "") if t.config else "--"]
                for ts, t in C.discover_tests().items()]
        lines.append(C.align_table(["测试", "生效快照"], rows))

    text = "\n".join(lines) + "\n"
    out = C.output_path(args.out, C.ROOT / "reports" / "config_changes.md",
                        print_only=args.print_only)
    if out:
        C.write_text(out, text)
        print(f"（{len(snaps)} 个快照）")
    else:
        print(text)


if __name__ == "__main__":
    main()
