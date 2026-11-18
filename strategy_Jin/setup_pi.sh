#!/usr/bin/env bash
# 树莓派 bash 脚本：安装所选项目的 Python 依赖，不编译引擎、不连接硬件。
set -euo pipefail
# 第一参数 strategy 或 web；默认 strategy。
profile="${1:-strategy}"
# 根据脚本路径确定迁移包根目录，支持带空格的目录。
bundle_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# 校验运行系统，避免把 Windows 虚拟环境当成树莓派环境使用。
if [[ "$(uname -s)" != "Linux" ]]; then
    echo '请在树莓派 Linux 的 bash 中执行本脚本。'
    exit 1
fi
# 避免用户用 sudo 安装并产生 root 拥有的项目文件。
if [[ "$(id -u)" == '0' ]]; then
    echo '请以普通用户运行 bash setup_pi.sh；系统依赖请按说明单独用 sudo apt 安装。'
    exit 1
fi
# Python 必须满足该迁移包的版本要求。
python3 -c 'import sys; assert sys.version_info >= (3,10), "需要 Python 3.10 以上"'
# 两套项目各有独立虚拟环境，不相互覆盖。
case "$profile" in
    strategy) project_dir="$bundle_dir/xiangqi_strategy"; requirements='requirements.txt' ;;
    web) project_dir="$bundle_dir/chch-robot"; requirements='requirements-pi.txt' ;;
    *) echo '用法：bash setup_pi.sh strategy 或 bash setup_pi.sh web'; exit 2 ;;
esac
# 完整网页按64位树莓派系统准备；策略项目仍可在匹配引擎的32位系统单独调试。
if [[ "$profile" == 'web' ]]; then
    python3 -c 'import struct; assert struct.calcsize("P") == 8, "完整网页请使用64位用户空间，保留厂商系统前先确认兼容性"'
fi
# 已有虚拟环境不擅自替换；错误环境需要用户备份后重建。
if [[ ! -d "$project_dir/.venv" ]]; then
    # system-site-packages 便于使用厂商已安装的 Arm_Lib；仍需实测当前解释器能否导入。
    python3 -m venv --system-site-packages "$project_dir/.venv"
fi
# 如果误复制了 Windows 虚拟环境，立即停止并给出提示。
if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
    echo '虚拟环境不可用，请勿复制 Windows .venv；备份后在树莓派重建。'
    exit 1
fi
# 所有 pip 操作只针对所选项目的虚拟环境。
"$project_dir/.venv/bin/python" -m pip install -r "$project_dir/$requirements"
# 引擎尚未部署也能先检测依赖，不连接摄像头、串口或机械臂。
"$project_dir/.venv/bin/python" "$bundle_dir/pi_project.py" check --project "$profile" --skip-engine
# 打印后续步骤，不自动开始实物对局。
echo '依赖安装检查完成。将ARM引擎放到engine_assets/pikafish，然后用所选项目的.venv/bin/python运行 pi_project.py check --project strategy或web --search。'
