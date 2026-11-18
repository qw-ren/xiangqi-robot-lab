# 视觉与 Web / 控制的接口（v1）

## 本阶段职责

视觉负责图像采集、棋盘定位、10×9 分类和质量信息，提供可复现原始样本及错格记录。
Web 负责展示、交互和用户确认；游戏/策略层负责轮次、合法走子和有效棋局更新。
机械臂层负责运动规划、执行与反馈。视觉不直接发送运动指令。

当前重点是可靠的视觉观测与 Web 对接。`vision` 分支用于视觉开发和推送，经团队评审后再合入 `main`。
本轮不改队友的 Web 路由或控制实现。

## 坐标约定

| 字段 | 原点与方向 | 取值 / 单位 |
| --- | --- | --- |
| `cells[].row/col`、`layout` | 图像中的棋盘左上，行向下、列向右 | row=0…9，col=0…8，整数交点索引 |
| `board_state` 的键 | 规范棋盘左上：黑方底线在上、红方底线在下 | `"col,row"`，与 `web_simulation/domain.py` 一致 |
| `calibration.board_pixel_corners` | 当前原图左上，x 向右、y 向下 | 像素，浮点；TL/TR/BL/BR 顺序 |
| 未来物理坐标 | 待双方实测定义的棋盘 / 机械臂坐标系 | 毫米，当前不提供 |

Web 用离散格点，不需要摄像头像素坐标。例如 `"4,7": "C"` 是第 5 列、第 8 行的红炮。
`--board-orientation black-top` 表示图像上方是黑方底线；`red-top` 将行列同时翻转 180° 后输出 `board_state`。
默认 `unknown` 时 `board_state=null`，仍提供图像方向的 `cells` 供调试。方向必须由人根据现场棋盘确认，不能靠当前模型预测猜测。
90° 横置棋盘须先调整标定 / 图像方向，当前没有该方向选项。

棋子符号：红方 `K A B N R C P`，黑方对应小写；依次为帅/将、仕/士、相/象、马、车、炮、兵/卒。
`.` 表示空位，`x` 表示未知。90 个交点都在 `cells` 中；`board_state` 仅包含已分类的棋子，**不能因字典中缺某个键就断言该点为空**，须结合 `cells` 的 `.` / `x` 和整帧状态。

## 可运行接口

生产端：`tools/vision/aruco_live.py --latest-json artifacts/vision/latest.json`。
它为每帧写完整快照，通过临时文件 + 原子替换避免 Web 读到半个 JSON。
启用此选项时每帧重新检查 ArUco 和棋盘几何，不沿用失效角点；缺码时发布拒绝状态。
退出、读帧错误或主动重新定位会使快照失效，不把上一帧伪装为当前观测。
这可能比固定机位缓存模式更慢，尚无本轮实机帧率结论。

同进程接口为 `tools.vision.observation.ObservationStream.observe(...)`，只依赖标准库，可由另一套摄像头服务传入分类结果；它本身不执行模型推理。
目前交付的是 Python / 本机文件接口，**没有新增 HTTP 路由**。Web 后端可读取文件，再通过自己的 API 发给浏览器；异机协作需队友在后端提供传输层。

```python
import json
from datetime import datetime, timezone
from pathlib import Path

snapshot = json.loads(Path("artifacts/vision/latest.json").read_text(encoding="utf-8"))
age = (datetime.now(timezone.utc) - datetime.fromisoformat(snapshot["captured_at"])).total_seconds()
fresh = 0 <= age <= 5  # 初始联调值；按实际处理耗时调整，不是已验收指标
if fresh and snapshot["status"] != "rejected":
    render_board = snapshot["board_state"]  # 可能为 None；展示为原始预测，并标注 reasons
# 当前版本 can_update_game 始终为 False；不得直接覆盖游戏状态或触发控制。
```

简化输出示例（完整文件还含 90 格、原始布局、定位信息与质量参数）：

```json
{
  "schema_version": "1.0",
  "frame_id": 42,
  "captured_at": "2026-11-18T12:00:00+08:00",
  "status": "provisional",
  "reasons": ["unconfirmed_geometry"],
  "orientation": "black-top",
  "board_state": {"4,7": "C"},
  "stable": true,
  "can_update_game": false
}
```

消费者必须检查时间戳（进程崩溃时无法主动作废最后一帧），并同时检查状态和方向。
缺帧、过期或 `source_stopped` 时可以保留旧棋盘作为历史展示，但须标记失效，不触发走子。
保存样本的 `result.json.observation` 与原图、矫正图及分数来自同一帧。

## 质量状态与当前限制

| status | 意义 | Web 行为 |
| --- | --- | --- |
| `rejected` | 未定位、未分类、有未知格或低分格 | 暂停使用，提示原因 |
| `provisional` | 临时标定、方向未确认，或尚未满足连续帧数 | 可显示调试预测，不作为确认棋局 |
| `review_required` | 基本检查通过且连续多帧相同 | 仍需人工 / 游戏层确认 |

默认连续 3 帧、最低模型分数 0.8，可配置。它们只是联调参数，未经过独立样本集标定；模型分数也不是正确率。
暂未实现手部遮挡检测、合法状态变化校验或自动接受棋局；`can_update_game` 始终为 `false`。
连续高分的“炮变相”依然可能出现，不能用稳定或分数掩盖该问题。后续工作是以已标注现场数据验证遮挡拒识、无遮挡错格与误接受率，再决定模型改进。
现有 `web_simulation/routes/recognition.py` 仍使用原识别器，不会自动切换到此接口。接入时应保留 Web 原有效棋局，显式选择何时确认新观测。

## 未来控制接口

规划走子可以先用离散的起点 / 终点，例如 `(col,row)`。落地执行才需要实测映射到机械臂坐标。
理想交点位置可由格距与棋盘坐标系算出；实际偏心棋子的抓取需要连续的棋子中心检测 / 测量。
当前整盘模型只分类每个固定格点，ArUco 给出的棋盘交点像素坐标不是棋子实际中心。
将来提供毫米坐标时须同时包含坐标系、单位、标定版本、时间戳、有效状态与实测误差，并定义高度和抓取策略。
目前不输出伪精确的抓取坐标，也不接入控制。
