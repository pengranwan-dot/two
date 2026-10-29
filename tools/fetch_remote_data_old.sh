#!/bin/bash
set -euo pipefail

REMOTE="${REMOTE:-b1-aibox}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST_DIR="$REPO_DIR/data_$(date +%Y%m%d)"
MODE="all"
DRY=0

usage() {
    cat <<EOF
用法: $0 [-l] [-b] [-c] [-n] [-d 数据根目录]
  -l          只同步 logs
  -b          只同步 bags
  -c          只同步 configs
  -n          仅预览（rsync --dry-run；tar 回退模式跳过拷贝）
  -d DIR      数据根目录（默认: <仓库>/data_<今天>；logs/bags/configs 分别落到 DIR 下）
  -h          显示帮助
 环境变量 REMOTE 可覆盖远程主机（默认: b1@192.168.1.10）

示例:
  $0                     # 同步 logs + bags + configs 到 data_<今天>/
  $0 -l -d data_1013     # 只同步 logs 到 data_1013/logs/
  $0 -b                  # 只同步 bags
  $0 -c                  # 只同步 configs
  $0 -n                  # 预览将要同步的内容
  REMOTE=b1-aibox $0     # 使用 ssh config 别名
EOF
    exit 1
}

while getopts "lbcnd:h" opt; do
    case "$opt" in
        l) MODE="logs" ;;
        b) MODE="bags" ;;
        c) MODE="configs" ;;
        n) DRY=1 ;;
        d) DEST_DIR="$OPTARG" ;;
        h|*) usage ;;
    esac
done

if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE" true 2>/dev/null; then
    echo "错误: 无法连接 $REMOTE（检查网络/ssh 配置）" >&2
    exit 1
fi

sync_dir() {
    local rel="$1"
    local dest="$2"
    local label="$3"

    echo "==> [$label] $REMOTE:~/$rel/ -> $dest/"
    mkdir -p "$dest"

    if ssh -o BatchMode=yes "$REMOTE" 'command -v rsync >/dev/null 2>&1'; then
        if [ "$DRY" -eq 1 ]; then
            rsync -rt --dry-run --info=progress2 "$REMOTE:$rel/" "$dest/"
        else
            rsync -rt --partial --info=progress2 "$REMOTE:$rel/" "$dest/"
        fi
    elif [ "$DRY" -eq 1 ]; then
        echo "    （dry-run：远端无 rsync，正式运行时将使用 tar 流式全量拷贝）"
    else
        echo "    远端无 rsync，改用 tar 流式拷贝（全量）"
        ssh -o BatchMode=yes "$REMOTE" "tar czf - -C \$HOME/$rel ." | tar xzf - -C "$dest"
    fi
}

if [ "$MODE" = "all" ] || [ "$MODE" = "logs" ]; then
    sync_dir "autonomous/logs" "$DEST_DIR/logs" "logs"
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "bags" ]; then
    sync_dir "autonomous/bags" "$DEST_DIR/bags" "bags"
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "configs" ]; then
    sync_dir "autonomous/configs" "$DEST_DIR/configs" "configs"
fi

echo "==> 完成"
du -sh "$DEST_DIR/logs" "$DEST_DIR/bags" "$DEST_DIR/configs" 2>/dev/null || true
