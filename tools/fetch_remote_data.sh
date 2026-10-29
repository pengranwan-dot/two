#!/bin/bash
set -euo pipefail

REMOTE="${REMOTE:-b1@192.168.1.10}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST_DIR="$REPO_DIR/data_$(date +%Y%m%d)"
MODE="all"
INCLUDE_FULL=0
DRY=0
BAGS=()

usage() {
    cat <<EOF
用法: $0 [-l] [-b] [-c] [-f] [-n] [-d 数据根目录] [-B 包名]...
  -l          只同步 logs
  -b          只同步 bags
  -c          只同步 configs
  -f          同时同步 autorecord_full_* 全包（默认过滤，不同步）
  -B NAME     只同步 autonomous/bags 下指定的包（可重复指定多个 -B）
              可只写 base 名：目录和同名 .zip 都存在时两者一起拉
              指定 -B 时忽略 -l/-c/-f，只同步这些包
  -n          仅预览（rsync --dry-run；tar 回退模式跳过拷贝）
  -d DIR      数据根目录（默认: <仓库>/data_<今天>；logs/bags/configs 分别落到 DIR 下）
  -h          显示帮助
  环境变量 REMOTE 可覆盖远程主机（默认: b1@192.168.1.10）

示例:
  $0                     # 同步 logs + bags + configs 到 data_<今天>/（不含全包）
  $0 -l -d data_1013     # 只同步 logs 到 data_1013/logs/
  $0 -b                  # 只同步 bags（不含全包）
  $0 -b -f               # 同步 bags（含 autorecord_full_* 全包）
  $0 -c                  # 只同步 configs
  $0 -B autorecord_full_20261027_112059
                         # 只拉这一个包（目录与同名 zip 都拉）
  $0 -B autorecord_full_20261027_112059.zip
                         # 只拉这个 zip 文件
  $0 -B pkg_a -B pkg_b -d data_1013
                         # 只拉指定的多个包到 data_1013/bags/
  $0 -n                  # 预览将要同步的内容
  REMOTE=b1-aibox $0     # 使用 ssh config 别名
EOF
    exit 1
}

while getopts "lbcnd:fhB:" opt; do
    case "$opt" in
        l) MODE="logs" ;;
        b) MODE="bags" ;;
        c) MODE="configs" ;;
        f) INCLUDE_FULL=1 ;;
        n) DRY=1 ;;
        d) DEST_DIR="$OPTARG" ;;
        B) BAGS+=("$OPTARG") ;;
        h|*) usage ;;
    esac
done

if [ "${#BAGS[@]}" -gt 0 ] && [ "$MODE" != "bags" ]; then
    echo "提示: 已指定 -B，忽略 -$MODE，只同步指定的包" >&2
    MODE="bags"
fi

if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE" true 2>/dev/null; then
    echo "错误: 无法连接 $REMOTE（检查网络/ssh 配置）" >&2
    exit 1
fi

HAS_RSYNC=0
if ssh -o BatchMode=yes "$REMOTE" 'command -v rsync >/dev/null 2>&1'; then
    HAS_RSYNC=1
fi

sync_dir() {
    local rel="$1"
    local dest="$2"
    local label="$3"
    local exclude="${4:-}"

    echo "==> [$label] $REMOTE:~/$rel/ -> $dest/"
    [ -n "$exclude" ] && echo "    （排除: $exclude）"
    mkdir -p "$dest"

    local rsync_opts=()
    [ -n "$exclude" ] && rsync_opts+=(--exclude "$exclude")

    if [ "$HAS_RSYNC" -eq 1 ]; then
        if [ "$DRY" -eq 1 ]; then
            rsync -rt --dry-run --info=progress2 "${rsync_opts[@]}" "$REMOTE:$rel/" "$dest/"
        else
            rsync -rt --partial --info=progress2 "${rsync_opts[@]}" "$REMOTE:$rel/" "$dest/"
        fi
    elif [ "$DRY" -eq 1 ]; then
        echo "    （dry-run：远端无 rsync，正式运行时将使用 tar 流式全量拷贝）"
    else
        echo "    远端无 rsync，改用 tar 流式拷贝（全量）"
        local tar_exclude=""
        [ -n "$exclude" ] && tar_exclude="--exclude './$exclude'"
        ssh -o BatchMode=yes "$REMOTE" "tar czf - $tar_exclude -C \$HOME/$rel ." | tar xzf - -C "$dest"
    fi
}

# 只同步 autonomous/bags 下指定条目（目录或文件）
sync_bag() {
    local name="$1"
    local base="${name%.zip}"
    local rbase="autonomous/bags"
    local dest="$DEST_DIR/bags"

    local probe
    probe=$(ssh -o BatchMode=yes "$REMOTE" "
        d=\"\$HOME/$rbase\"
        [ -d \"\$d/$name\" ] && echo \"DIR|$name\"
        [ -f \"\$d/$name\" ] && echo \"FILE|$name\"
        [ -f \"\$d/$base.zip\" ] && echo \"FILE|$base.zip\"
        true" | sort -u)

    if [ -z "$probe" ]; then
        echo "错误: 远端 ~/$rbase 下找不到 '$name'" >&2
        echo "远端相近的包:" >&2
        ssh -o BatchMode=yes "$REMOTE" "ls -1 \$HOME/$rbase 2>/dev/null | grep -F '$base' | head -20" >&2 || true
        exit 1
    fi

    mkdir -p "$dest"
    local kind entry
    while IFS='|' read -r kind entry; do
        [ -z "${kind:-}" ] && continue
        if [ "$kind" = "DIR" ]; then
            echo "==> [bag:$entry] $REMOTE:~/$rbase/$entry/ -> $dest/$entry/"
            if [ "$HAS_RSYNC" -eq 1 ]; then
                if [ "$DRY" -eq 1 ]; then
                    rsync -rt --dry-run --info=progress2 "$REMOTE:$rbase/$entry/" "$dest/$entry/"
                else
                    rsync -rt --partial --info=progress2 "$REMOTE:$rbase/$entry/" "$dest/$entry/"
                fi
            elif [ "$DRY" -eq 1 ]; then
                echo "    （dry-run：远端无 rsync，正式运行时将使用 tar 流式拷贝）"
            else
                echo "    远端无 rsync，改用 tar 流式拷贝"
                ssh -o BatchMode=yes "$REMOTE" "tar czf - -C \$HOME/$rbase ./$entry" | tar xzf - -C "$dest"
            fi
        else
            echo "==> [bag:$entry] $REMOTE:~/$rbase/$entry -> $dest/"
            if [ "$HAS_RSYNC" -eq 1 ]; then
                if [ "$DRY" -eq 1 ]; then
                    rsync -t --dry-run --info=progress2 "$REMOTE:$rbase/$entry" "$dest/"
                else
                    rsync -t --partial --info=progress2 "$REMOTE:$rbase/$entry" "$dest/"
                fi
            elif [ "$DRY" -eq 1 ]; then
                echo "    （dry-run：远端无 rsync，正式运行时将使用 tar 流式拷贝）"
            else
                echo "    远端无 rsync，改用 tar 流式拷贝"
                ssh -o BatchMode=yes "$REMOTE" "tar czf - -C \$HOME/$rbase ./$entry" | tar xzf - -C "$dest"
            fi
        fi
    done < <(printf '%s\n' "$probe")
}

if [ "$MODE" = "all" ] || [ "$MODE" = "logs" ]; then
    sync_dir "autonomous/logs" "$DEST_DIR/logs" "logs"
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "bags" ]; then
    if [ "${#BAGS[@]}" -gt 0 ]; then
        for b in "${BAGS[@]}"; do
            sync_bag "$b"
        done
    elif [ "$INCLUDE_FULL" -eq 1 ]; then
        sync_dir "autonomous/bags" "$DEST_DIR/bags" "bags" ""
    else
        sync_dir "autonomous/bags" "$DEST_DIR/bags" "bags" "autorecord_full_*"
    fi
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "configs" ]; then
    sync_dir "autonomous/configs" "$DEST_DIR/configs" "configs"
fi

echo "==> 完成"
du -sh "$DEST_DIR/logs" "$DEST_DIR/bags" "$DEST_DIR/configs" 2>/dev/null || true
