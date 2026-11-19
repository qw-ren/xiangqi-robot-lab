param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Dataset,
    [Parameter(Mandatory=$true)][string]$Out,
    [ValidateRange(1, 1000)][int]$Epochs = 12
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$root = Join-Path $repo 'artifacts/training'
$upstream = Join-Path $root 'upstream-code'
$datasetPath = (Resolve-Path -LiteralPath $Dataset).Path.Replace('\','/')
$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Out)
if (Test-Path -LiteralPath $outputPath) { throw "Choose a new output folder: $outputPath" }
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$env:PYTHONPATH = (Join-Path $root 'openmmlab-deps') + [IO.Path]::PathSeparator + $upstream
$env:PYTHONUTF8 = '1'
& $pythonPath -c "import torch; assert torch.cuda.is_available(), 'CUDA required'; print(torch.cuda.get_device_name(0))"
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'upstream_mixed_config.py') -Destination (Join-Path $upstream 'cchess_reg/configs/lab-mixed.py')
Push-Location (Join-Path $upstream 'cchess_reg')
try {
    & $pythonPath -u tools/train.py configs/lab-mixed.py --work-dir $outputPath --amp --cfg-options "train_dataloader.dataset.data_root=$datasetPath" "val_dataloader.dataset.data_root=$datasetPath" "train_cfg.max_epochs=$Epochs" "param_scheduler.0.end=$Epochs"
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
