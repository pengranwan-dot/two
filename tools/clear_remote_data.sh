#!/usr/bin/env bash
#
# clear_remote_data.sh —— 从本机远程清空域控/车端指定数据目录下的"内容"
#
# 用途：在本机运行本脚本，远程清空自治系统车辆的以下三个目录中的全部内容：
#         <BASE_DIR>/logs
#         <BASE_DIR>/bags
#         <BASE_DIR>/configs
#       注意：只删除这三个目录"内部"的内容，目录本身保留，其它路径一律不动。
#
# 依赖：ssh + sshpass（本机需安装 sshpass）
#
# 用法：
#   ./clear_remote_data.sh                 # 直接清空（无需确认）
#   ./clear_remote_data.sh -n              # 演练：只列出将被删除的内容，不实际删除
#   ./clear_remote_data.sh -h              # 查看帮助
#
# 可用环境变量覆盖默认值：
#   REMOTE_HOST   域控/车端 IP      （默认 192.168.1.10）
#   REMOTE_USER   登录用户名        （默认 b1）
#   REMOTE_PASS   登录密码          （默认 b1@AIBOX）
#   REMOTE_PORT   SSH 端口          （默认 22）
#   BASE_DIR      自治系统根目录    （默认 /home/b1/autonomous）
#   TARGET_DIRS   要清空的子目录    （默认 "logs bags configs"）
#
# 示例：
#   REMOTE_HOST=192.168.1.10 ./clear_remote_data.sh
#   ./clear_remote_data.sh -n
#
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-192.168.1.10}"
REMOTE_USER="${REMOTE_USER:-b1}"
REMOTE_PASS="${REMOTE_PASS:-b1@AIBOX}"
REMOTE_PORT="${REMOTE_PORT:-22}"
BASE_DIR="${BASE_DIR:-/home/b1/autonomous}"
# shellcheck disable=SC2206
TARGET_DIRS=(${TARGET_DIRS:-logs bags configs})

DRY_RUN=0

usage() {
    cat <<'EOF'
clear_remote_data.sh —— 从本机远程清空域控/车端指定数据目录下的“内容”

用途：在本机运行本脚本，远程清空自治系统车辆的以下三个目录中的全部内容：
        <BASE_DIR>/logs
        <BASE_DIR>/bags
        <BASE_DIR>/configs
      注意：只删除这三个目录“内部”的内容，目录本身保留，其它路径一律不动。

依赖：ssh + sshpass（本机需安装 sshpass）

用法：
  ./clear_remote_data.sh                 # 直接清空（无需确认）
  ./clear_remote_data.sh -n              # 演练：只列出将被删除的内容，不实际删除
  ./clear_remote_data.sh -h              # 查看帮助

可用环境变量覆盖默认值：
  REMOTE_HOST   域控/车端 IP      （默认 192.168.1.10）
  REMOTE_USER   登录用户名        （默认 b1）
  REMOTE_PASS   登录密码          （默认 b1@AIBOX）
  REMOTE_PORT   SSH 端口          （默认 22）
  BASE_DIR      自治系统根目录    （默认 /home/b1/autonomous）
  TARGET_DIRS   要清空的子目录    （默认 "logs bags configs"）

示例：
  REMOTE_HOST=192.168.1.10 ./clear_remote_data.sh
  ./clear_remote_data.sh -n
EOF
    exit 0
}

while getopts ":nh" opt; do
    case "$opt" in
        n) DRY_RUN=1 ;;
        h) usage ;;
        *) echo "未知参数: -$OPTARG（用 -h 查看帮助）" >&2; exit 2 ;;
    esac
done

if ! command -v sshpass >/dev/null 2>&1; then
    echo "错误: 未找到 sshpass，请先安装（Ubuntu: sudo apt install sshpass）" >&2
    exit 1
fi
if ! command -v ssh >/dev/null 2>&1; then
    echo "错误: 未找到 ssh 客户端" >&2
    exit 1
fi

SSH_OPTS=(-p "$REMOTE_PORT"
          -o StrictHostKeyChecking=no
          -o UserKnownHostsFile=/dev/null
          -o ConnectTimeout=10
          -o LogLevel=ERROR)

remote() {  # 在远端执行一段命令，返回其输出；stdin 接 /dev/null 避免 ssh 吞掉本脚本的输入
    sshpass -p "$REMOTE_PASS" ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$REMOTE_HOST" "$@" < /dev/null
}

echo "==> 目标: ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PORT}"
echo "==> 基础目录: ${BASE_DIR}"
echo "==> 待清空子目录: ${TARGET_DIRS[*]}"
[ "$DRY_RUN" -eq 1 ] && echo "==> 模式: 演练（不实际删除）"

# 1) 连通性检查
if ! remote true 2>/dev/null; then
    echo "错误: 无法通过 SSH 连接到 ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PORT}" >&2
    echo "      请检查网络、IP、端口、用户名/密码是否正确。" >&2
    exit 1
fi

# 2) 校验基础目录存在
if ! remote "test -d '${BASE_DIR}'"; then
    echo "错误: 远端目录不存在: ${BASE_DIR}" >&2
    exit 1
fi

# 3) 展示各子目录当前的条目数量（便于确认清空效果）
echo "==> 清空前各目录内容："
for d in "${TARGET_DIRS[@]}"; do
    path="${BASE_DIR}/${d}"
    if remote "test -d '${path}'"; then
        cnt=$(remote "find '${path}' -mindepth 1 | wc -l")
        echo "    ${path}: ${cnt} 项"
    else
        echo "    ${path}: [目录不存在，将跳过]"
    fi
done

# 4) 执行清空（只删子目录内部内容，保留目录本身；目录不存在则跳过）
status=0
for d in "${TARGET_DIRS[@]}"; do
    path="${BASE_DIR}/${d}"
    if ! remote "test -d '${path}'"; then
        echo "==> 跳过（不存在）: ${path}"
        continue
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "==> [演练] 将删除 ${path} 下的内容："
        remote "find '${path}' -mindepth 1 -maxdepth 1 -printf '        %y %p\n' 2>/dev/null || find '${path}' -mindepth 1 -maxdepth 1"
    else
        # -mindepth 1 保证不删除 ${path} 自身；使用引号防止路径中被误解
        if remote "find '${path}' -mindepth 1 -delete"; then
            echo "==> 已清空: ${path}"
        else
            echo "错误: 清空失败: ${path}" >&2
            status=1
        fi
    fi
done

# 5) 复核
if [ "$DRY_RUN" -eq 0 ]; then
    echo "==> 清空后各目录内容："
    for d in "${TARGET_DIRS[@]}"; do
        path="${BASE_DIR}/${d}"
        if remote "test -d '${path}'"; then
            cnt=$(remote "find '${path}' -mindepth 1 | wc -l")
            echo "    ${path}: ${cnt} 项"
        fi
    done
fi

exit "$status"
