# 项目结构与整合记录

## 单份源码，按功能组织

Git 分支是同一份工作目录的不同版本，不对应 `vision/整个项目` 或 `strategy_Jin/整个项目` 两套副本。
视觉、Web 和控制同学在自己的 Git 分支修改共用模块，通过 PR 合并。

| 目录 / 文件 | 内容 |
| --- | --- |
| `run_local.py` | 统一 Web 启动入口；支持纯视觉预览 / mock / 原有后端 |
| `vision/aruco.py` | 共用 ArUco 几何与整盘分类流程 |
| `vision/observation.py` | 离散坐标、质量与原子 JSON 输出；仅依赖标准库 |
| `vision/recognizer.py` | Web 识别服务，显式选择 ArUco / RTMPose |
| `vision/camera*.py`、`http_capture.py` | 共用采集，避免两个进程抢同一相机 |
| `core/`、`model/` | 底层算法与单份 ONNX 资产 |
| `web_simulation/` | Flask、HTML / JS，观测与正式游戏状态分离 |
| `xiangqi_strategy/` | 队友的独立策略 / 串口服务，仍是独立棋局 |
| `ai/`、`engine_assets/` | 引擎适配与公共权重，平台可执行文件自行配置 |
| `robot/` | TCP/串口控制适配；`robot/arm_teaching/` 是独立树莓派工具，通过 I2C 控制 1..5 号舵机、通过 BCM GPIO21 控制电磁铁，不接入现有自动走棋链 |
| `firmware/` | 固件；本次机械臂示教整合不修改固件 |
| `xq_utils/` | FEN、坐标、记谱及队友引入的 cchess 规则层 |
| `tools/vision/`、`tools/web/`、`tools/deploy/` | 命令行入口与运行 / 部署辅助 |
| `configs/aruco/`、`configs/dofbot/` | 共享配置示例，不能冒充现场标定 |
| `configs/local/`、`artifacts/`、`data/` | 忽略的个人配置、样本与运行状态 |
| `docs/integration/import-2026-11-18/` | 队友原迁移包说明，作为历史记录保留 |

## 2026-11-18 目录迁移

| 原位置 | 现位置 |
| --- | --- |
| `strategy_Jin/chch-robot/web_simulation/` | 根目录 `web_simulation/`，保留队友新增功能 |
| 其中 `vision/ core/ model/ robot/ ai/ xq_utils/` | 对应根目录共用模块；相同副本去重 |
| `dofbot_web.py` | `web_simulation/dofbot.py` |
| `dofbot_adapter.py` | `robot/dofbot_adapter.py` |
| `chro_web_tools.py` | `xq_utils/rules.py`，命令自测用 `python -m xq_utils.rules --self-test` |
| `local_engine_io.py` | `ai/local_engine_io.py` |
| `watch_service.py` | `tools/web/watch_service.py` |
| `dofbot_calibration.example.json` | `configs/dofbot/example.json` |
| `strategy_Jin/xiangqi_strategy/` | `xiangqi_strategy/` |
| `strategy_Jin/engine_assets/` | `engine_assets/` |
| `strategy_Jin/pi_project.py`、`setup_pi.sh` | `tools/deploy/`，根目录计算和启动路径已更新 |
| `tools/vision/aruco_image_test.py` 中的几何实现 | `vision/aruco.py`，工具保留测试与命令入口 |
| `tools/vision/observation.py` | `vision/observation.py`，旧导入路径保留兼容转发 |

导入包中 32 个相同文件（忽略换行符差异）已去重，包括重复 ONNX 模型；不同内容逐项整合。
原始版本仍可在 Git 的 `26c8dfb` / 合并提交 `edf7fe2` 中查阅。
当前仓库根目录的少数旧本机忽略文件不作为源码或交接入口。

## 分支操作

本次 `edf7fe2` 是将 `strategy_Jin` 合入 `main`；本地 `pull --tags origin main` 是正常快进。
当时 `vision` 的 `4559b8a` 尚未合入 `main`，因此仅切回 `main` 看不到该视觉接口。
本轮已切回 `vision` 并合入最新 `main`，再完成整合；需要通过 `vision → main` 的 PR 交给团队。

通常的后续步骤：

```bash
git switch main
git pull --ff-only origin main
git switch vision
git merge main
# 修改、测试、git add 指定文件、git commit
git push origin vision
# GitHub 发起 vision -> main 的 Pull Request，检查差异与 CI 后合并
```

不用为每个分支复制整个项目文件夹。合并多人代码时先确保工作区干净；不要以覆盖文件夹代替审查差异。
