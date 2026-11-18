# 树莓派精简项目 · 核对版

日期：2026-11-18。推荐从本目录的`pi_project.py`启动，保留79个运行/工具文件和4份简短说明。程序中的中文注释没有删除。

## 先明确运行范围

- `xiangqi_strategy`：你负责的策略服务、轻量网页、串口全盘输入和落子计划输出。不需要自带相机识别；没有直接Arm_Lib执行器。
- `chch-robot`：完整CH-RO网页、自带相机/ONNX识别和DOFBOT适配。默认mock；真实后端需要厂商库、现场标定及相机确认。
- 两套程序各有棋局；启动两套不代表它们已经接通。完整网页尚未接入队友的串口视觉，总体控制协调仍需实现。这是原有集成缺项，不是精简造成的。

## 1. 上传与解压

电脑PowerShell，替换用户名和IP：

```powershell
$PiTarget = "实际用户名@实际树莓派IP"
scp "C:\Users\Lenovo\Documents\Codex\2026-11-15\w-x20\outputs\xiangqi_pi_runtime_checked_20261118.zip" "${PiTarget}:~/"
ssh -L 15001:127.0.0.1:5001 -L 15000:127.0.0.1:5000 $PiTarget
```

以下命令在树莓派bash执行。SSH窗口保持打开，访问通道才有效：

```bash
python3 --version
getconf LONG_BIT
dpkg --print-architecture
sudo apt update
sudo apt install -y unzip python3-venv python3-pip
# 完整网页的OpenCV还可能需要下列系统共享库，名称按实际发行版确认。
sudo apt install -y libgl1 libglib2.0-0
cd "$HOME"
unzip xiangqi_pi_runtime_checked_20261118.zip
cd "$HOME/xiangqi_pi_runtime_checked_20261118"
```

用新目录解压，不覆盖已有现场标定。迁移包要求Python3.10以上，建议3.11/3.12；完整网页按64位系统准备。若DOFBOT厂商系统较旧，先确认Arm_Lib和Python兼容，再决定部署方式。

## 2. 放入树莓派引擎

包中已有Pikafish-2026-09-06配套`engine_assets/pikafish.nnue`，没有ARM可执行文件。将已部署且匹配该权重版本的Linux ARM引擎放到`engine_assets/pikafish`：

```bash
chmod +x engine_assets/pikafish
```

Pi4B的64位用户空间使用ARM64/armv8，32位需匹配ARM32版本；不能使用Windows.exe或Linux x86引擎。不要只凭文件名判断架构。若已有文件不在包内，自检和启动都可添加`--engine /绝对路径/pikafish --nnue /绝对路径/pikafish.nnue`。

## 3. 启动策略项目

```bash
bash setup_pi.sh strategy
xiangqi_strategy/.venv/bin/python pi_project.py check --project strategy --search
xiangqi_strategy/.venv/bin/python pi_project.py start --project strategy
```

电脑浏览器打开`http://127.0.0.1:15001/`。树莓派本机用5001端口。`--search`实际调用真实引擎、检查合法走法，不使用fake_uci。

接队友串口时，在启动命令末尾增加`--serial /实际设备路径 --baud 115200`。使用`/dev/serial/by-id/...`等真实设备名，并确认当前用户有权限，串口未被其他程序占用。详细数据格式见《串口对接约定.md》。

## 4. 启动完整网页

另开终端，在包根目录执行：

```bash
bash setup_pi.sh web
chch-robot/.venv/bin/python pi_project.py check --project web --search
chch-robot/.venv/bin/python pi_project.py start --project web --backend mock
```

电脑浏览器打开`http://127.0.0.1:15000/`。选择虚拟棋盘输入并开始模拟，可不用摄像头。无下位机与是否使用摄像头是不同设置。

现有网页仍导入视觉模块，模拟时也要安装requirements-pi.txt。不要使用GPU版requirements或Windows虚拟环境；现有前端无需npm构建。

## 5. 停止与真实后端

前台运行时按Ctrl+C停止。完整网页也可在树莓派另一个终端中执行：

```bash
chch-robot/.venv/bin/python chch-robot/stop_local.py --port 5000
```

stop_local只用于完整网页，不用于策略服务。这些是前台调试命令，尚未安装Linux开机服务；关闭终端、断电或系统休眠会影响访问。

真实DOFBOT前，先安装厂商Arm_Lib并确认虚拟环境可见；准备现场90格上下姿态、park、吃子槽位和电磁铁配置。示例JSON默认gripper，不是现场数据。电磁铁应配置magnet及实际BCM引脚/极性。

```bash
chch-robot/.venv/bin/python -c "from Arm_Lib import Arm_Device; print('可以导入；尚未实例化设备')"
chch-robot/.venv/bin/python pi_project.py check --project web --backend dofbot --search
chch-robot/.venv/bin/python pi_project.py start --project web --backend dofbot \
  --calibration /现场文件绝对路径/dofbot_calibration.json
```

启动后仍需网页连接、标定检查、确认吃子槽已清空和初始棋盘确认。当前真实网页后端只接受本地/网络相机反馈，队友串口尚未接入它。

《核心文件与运行流程.md》用于定位代码，《精简核对报告.md》列出删减依据与验证范围。
