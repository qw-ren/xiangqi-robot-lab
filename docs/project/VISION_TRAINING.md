# 整盘棋子分类训练

在 Windows 本机的 `xiangqi-robot-lab` 仓库根目录打开 PowerShell。采集工具用项目里的便携 Python，训练用已装 CUDA 版 PyTorch 的 Python。运行时会打印 GPU 型号。

```powershell
$capturePy = '.\artifacts\python312\python.exe'
$trainPy = "$env:USERPROFILE\.conda\envs\pytorch\python.exe"
& $trainPy -c "import torch; print(torch.__version__); assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))"
```

本机上游环境放在 `artifacts/training/`。其中 `upstream-code/` 是 TheOne1006 的分类训练源码，`openmmlab-deps/` 是单独安装的 MMEngine 0.10.7、MMCV Lite 2.1.0、MMPretrain 1.2.0 及依赖，`reg_data.zip` 是上游公开数据包。这三个目录或文件需要保留，因为 Git 忽略 `artifacts/`。换电脑时要用对方本机 PyTorch 的解释器重新准备环境。

## 训练和启用 ONNX 分类校正层

先按下面的采集和导出步骤生成带有 `train` 和 `val` 的清单。校正层使用自采棋盘清单，可以直接用 `manifest-mixed.json`，无需先合并公开数据。便携 Python 提取 ONNX 分数和格点颜色特征，PyTorch 环境用 GPU 训练。

```powershell
$manifest = 'artifacts/training/manifest-mixed.json'
$adapterOut = 'artifacts/training/gpu-onnx-adapter-v2'
& $capturePy tools/vision/train_classifier_adapter.py --manifest $manifest --out $adapterOut --features-only
& $trainPy tools/vision/train_classifier_adapter.py --manifest $manifest --out $adapterOut --fit-only
```

用 `report.json` 对照独立验证集上的单格正确数、整盘正确数和错分类型。验证通过后，把输出的 `nano_v3-0319.adapter.npz` 放到 `model/layout_recognition/`，重启识别程序即可加载。要回放原模型，可以在启动进程前设置 `$env:CHRO_DISABLE_CLASSIFIER_ADAPTER = '1'`，恢复时用 `Remove-Item Env:CHRO_DISABLE_CLASSIFIER_ADAPTER` 后重启。

## 追加一组数据

```powershell
& $capturePy tools/vision/aruco_live.py --camera 0 --config configs/local/board-local.json --allow-approximate-geometry --out artifacts/training/session-06
```

按 **S** 保存，调整相机或棋盘后按 **R** 重定位，按 **Q** 退出。每个样本目录保存原图 `frame.png` 和矫正图 `warped.png`。在同目录写 `manual.txt`，每个文件 10 行，每行 9 个符号，按矫正图从左上到右下读格点。

- 红棋为 `K A B N R C P`，黑棋对应小写。
- 「车」和「車」映射到同一类，红车 `R`、黑车 `r`。
- `.` 是空位，`x` 用于遮挡或无法辨认。模型生成的 `layout.txt` 只供核对，真值写在 `manual.txt`。
- 按采集场次分组。训练与验证使用不同目录。若要验证换用「車」的效果，把同一套摆法、朝向范围和光照下另拍的简体与繁体样本留作测试。

## 导出、混合、训练

以下示例把第 06 组追加到训练集，仍由第 03 组验证。每一轮给混合数据和模型输出取新目录。

```powershell
& $capturePy tools/vision/export_labeled_boards.py `
  --train artifacts/training/session-01 --train artifacts/training/session-02 `
  --train artifacts/training/session-04 --train artifacts/training/session-05 `
  --train artifacts/training/session-06 --val artifacts/training/session-03 `
  --out artifacts/training/manifest-v2.json

& $capturePy tools/vision/export_openmmlab_boards.py `
  --manifest artifacts/training/manifest-v2.json --out artifacts/training/local-v2

& $capturePy tools/vision/merge_board_training.py `
  --public-zip artifacts/training/reg_data.zip --local artifacts/training/local-v2 `
  --out artifacts/training/mixed-v2

powershell -NoProfile -ExecutionPolicy Bypass -File tools/vision/train_upstream_mixed.ps1 `
  -Python $trainPy -Dataset artifacts/training/mixed-v2 -Out artifacts/training/gpu-v2 -Epochs 12
```

如果只复跑本次五组数据，省略 `--train artifacts/training/session-06`。合并脚本只把上游训练图加入训练集，把上游验证保存为 `public-val`，自采验证保存为 `val`。当前配置从头训练上游 SwinV2，使用相同的中心裁剪和缩放。模型权重、完整配置和训练日志保存在 `-Out` 指定目录。

## 用统一指标评估

```powershell
$training = (Resolve-Path artifacts/training).Path
$env:PYTHONPATH = "$training\openmmlab-deps;$training\upstream-code"
& $trainPy tools/vision/eval_upstream_mixed.py --dataset artifacts/training/mixed-v2 --run artifacts/training/gpu-v2
```

在输出目录打开 `board-validation.json`，查看自采和上游两个验证集的逐格正确数、整盘全对数和类别混淆。部署前用留出的新场次再评估一次。
