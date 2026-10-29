#!/usr/bin/env bash
# 一键对指定数据根目录运行全部可用的分析脚本（报告输出到 <root>/reports/）。
#
# 用法:
#   bash tools/run_all.sh                          # 自动探测最新 data_*
#   bash tools/run_all.sh --root data_1014
#   bash tools/run_all.sh --root data_1014 --no-extract --no-reports   # 快速模式
#   bash tools/run_all.sh --root data_1014 --date 20261014 \
#        --from 150000 --to 160000 --exclude 154155
#   bash tools/run_all.sh --root data_1015 --roll-roots data_1013,data_1014
#
# 步骤:
#   1) list_tests          → reports/tests.txt
#   2) extract_topics      → csv/<ts>/ 缓存（--no-extract 跳过）
#   3) test_summary        → reports/test_summary.md
#   4) auto_summary        → reports/auto_summary.md
#   5) params_table        → reports/params_table.md
#   6) phase2_stats        → reports/phase2_stats.md
#   7) config_diff         → reports/config_changes.md
#   8) gen_roll_temp_comp  → reports/roll_temp_comp.md（--no-roll-comp 跳过；
#                            --roll-roots 合并其它天一起寻优）
#   9) bag_info            → reports/bag_info.txt
#  10) test_report         → reports/test_report_<ts>.md（每个测试一个，--no-reports 跳过）
set -uo pipefail

TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-python3}"

ROOT=""
DATE=""
FROM=""
TO=""
EXCLUDE=""
ROLL_ROOTS=""
DO_EXTRACT=1
DO_REPORTS=1
DO_ROLL=1
REFRESH=0

usage() {
    sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-1}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --root) ROOT="$2"; shift 2 ;;
        --date) DATE="$2"; shift 2 ;;
        --from) FROM="$2"; shift 2 ;;
        --to) TO="$2"; shift 2 ;;
        --exclude) EXCLUDE="$2"; shift 2 ;;
        --roll-roots) ROLL_ROOTS="$2"; shift 2 ;;
        --no-extract) DO_EXTRACT=0; shift ;;
        --no-reports) DO_REPORTS=0; shift ;;
        --no-roll-comp) DO_ROLL=0; shift ;;
        --refresh-csv) REFRESH=1; shift ;;
        -h|--help) usage 0 ;;
        *) echo "未知参数: $1" >&2; usage 1 ;;
    esac
done

if [ -n "$ROOT" ]; then
    export DATA_ROOT="$ROOT"
fi
ROOT="$("$PY" - "$TOOLS_DIR" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import common as C
print(C.ROOT or "")
PY
)"
if [ -z "$ROOT" ]; then
    echo "错误: 未找到数据根目录，请用 --root <data_YYYYMMDD> 指定" >&2
    exit 1
fi
export DATA_ROOT="$ROOT"
REPORTS="$ROOT/reports"
mkdir -p "$REPORTS"

DATE_ARG=()
[ -n "$DATE" ] && DATE_ARG=(--date "$DATE")
SUM_FILTER=("${DATE_ARG[@]}")
[ -n "$FROM" ] && SUM_FILTER+=(--from "$FROM")
[ -n "$TO" ] && SUM_FILTER+=(--to "$TO")
[ -n "$EXCLUDE" ] && SUM_FILTER+=(--exclude "$EXCLUDE")
PT_FILTER=("${DATE_ARG[@]}")
[ -n "$FROM" ] && PT_FILTER+=(--from "$FROM")
[ -n "$TO" ] && PT_FILTER+=(--to "$TO")

echo "数据根目录 : $ROOT"
echo "报告输出   : $REPORTS"
echo "开始时间   : $(date '+%F %T')"

START_TS=$(date +%s)
FAILED=()

run() {
    echo
    echo ">>> $*"
    if ! "$@"; then
        FAILED+=("$*")
        echo "!! 失败: $*" >&2
    fi
}

capture() {
    local out="$1"
    shift
    echo
    echo ">>> $* → ${out#"$ROOT"/}"
    if ! "$@" >"$out" 2>&1; then
        FAILED+=("$*")
        echo "!! 失败: $*（详见 $out）" >&2
    fi
}

capture "$REPORTS/tests.txt" "$PY" "$TOOLS_DIR/list_tests.py" "${DATE_ARG[@]}"

if [ "$DO_EXTRACT" -eq 1 ]; then
    EXTRACT_ARGS=(--all "${DATE_ARG[@]}")
    [ "$REFRESH" -eq 1 ] && EXTRACT_ARGS+=(--force)
    run "$PY" "$TOOLS_DIR/extract_topics.py" "${EXTRACT_ARGS[@]}"
else
    echo
    echo "（--no-extract 跳过 CSV 缓存导出）"
fi

run "$PY" "$TOOLS_DIR/test_summary.py" "${SUM_FILTER[@]}"
run "$PY" "$TOOLS_DIR/auto_summary.py" "${SUM_FILTER[@]}"
run "$PY" "$TOOLS_DIR/params_table.py" "${PT_FILTER[@]}"
run "$PY" "$TOOLS_DIR/phase2_stats.py" --all "${PT_FILTER[@]}"
run "$PY" "$TOOLS_DIR/config_diff.py" --assoc

if [ "$DO_ROLL" -eq 1 ]; then
    ROLL_ARGS=(--root "$ROOT")
    if [ -n "$ROLL_ROOTS" ]; then
        IFS=',' read -r -a ROLL_EXTRA <<<"$ROLL_ROOTS"
        for r in "${ROLL_EXTRA[@]}"; do
            [ -n "$r" ] && ROLL_ARGS+=(--root "$r")
        done
        echo
        echo "（roll 温度补偿合并数据根目录：${ROLL_ROOTS}）"
    fi
    run "$PY" "$TOOLS_DIR/gen_roll_temp_comp.py" "${ROLL_ARGS[@]}"
else
    echo
    echo "（--no-roll-comp 跳过 roll 温度补偿参数生成）"
fi

capture "$REPORTS/bag_info.txt" "$PY" "$TOOLS_DIR/bag_info.py" --all

if [ "$DO_REPORTS" -eq 1 ]; then
    mapfile -t TESTS < <("$PY" - "$TOOLS_DIR" "$DATE" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import common as C
date = sys.argv[2] if len(sys.argv) > 2 else ""
for ts, t in C.discover_tests().items():
    if date and t.date != date:
        continue
    print(ts)
PY
)
    echo
    echo ">>> 逐测试报告（${#TESTS[@]} 个）"
    for ts in "${TESTS[@]}"; do
        if ! "$PY" "$TOOLS_DIR/test_report.py" "$ts" >/dev/null 2>&1; then
            FAILED+=("test_report.py $ts")
            echo "!! 失败: test_report.py $ts" >&2
        fi
    done
    echo "已写入 $REPORTS/test_report_*.md"
else
    echo
    echo "（--no-reports 跳过逐测试报告）"
fi

ELAPSED=$(( $(date +%s) - START_TS ))
echo
echo "===== 完成（${ELAPSED}s）====="
ls -1 "$REPORTS"
if [ "${#FAILED[@]}" -gt 0 ]; then
    echo
    echo "有 ${#FAILED[@]} 个步骤失败:" >&2
    for f in "${FAILED[@]}"; do
        echo "  - $f" >&2
    done
    exit 1
fi
