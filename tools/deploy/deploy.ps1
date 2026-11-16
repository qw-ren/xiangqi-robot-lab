param(
    [string]$HostName,
    [string]$IdentityFile,
    [string]$ReleaseBase = '/home/dofbot/xiangqi-robot-lab/releases',
    [switch]$PackageOnly
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Push-Location -LiteralPath $repoRoot
try {
    $dirty = @(git status --porcelain)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Git status.' }
    if ($dirty.Count -gt 0) { throw 'Commit or stash local changes before packaging.' }
    $revision = (git rev-parse HEAD).Trim()
    if ($revision -notmatch '^[0-9a-f]{40}$') { throw 'Invalid commit SHA.' }
    $artifactDir = Join-Path $repoRoot 'artifacts\deploy'
    New-Item -ItemType Directory -Path $artifactDir -Force | Out-Null
    $archivePath = Join-Path $artifactDir ($revision + '.tar.gz')
    git archive --format=tar.gz -o $archivePath HEAD
    if ($LASTEXITCODE -ne 0) { throw 'git archive failed.' }
    Write-Output "Package: $archivePath"
    Write-Output "Commit: $revision"
    if ($PackageOnly) { return }
    if ($HostName -notmatch '^[A-Za-z0-9_.@:-]+$') { throw 'Supply a valid SSH host.' }
    if ($ReleaseBase -notmatch '^/[A-Za-z0-9_./-]+$' -or $ReleaseBase.Contains('..')) { throw 'Invalid release base.' }
    if (-not (Test-Path -LiteralPath $IdentityFile -PathType Leaf)) { throw 'Supply a readable SSH identity file.' }
    $identity = (Resolve-Path -LiteralPath $IdentityFile).Path
    $remoteRelease = "$ReleaseBase/$revision"
    $remoteArchive = "$ReleaseBase/$revision.tar.gz"
    ssh -i $identity -o BatchMode=yes $HostName "mkdir -p '$ReleaseBase'; test ! -e '$remoteRelease'"
    if ($LASTEXITCODE -ne 0) { throw 'SSH failed or this release already exists.' }
    scp -i $identity $archivePath "${HostName}:$remoteArchive"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed.' }
    ssh -i $identity -o BatchMode=yes $HostName "mkdir '$remoteRelease' && tar -xzf '$remoteArchive' -C '$remoteRelease'"
    if ($LASTEXITCODE -ne 0) { throw 'Remote extraction failed.' }
    Write-Output "Deployed source only: $remoteRelease"
    Write-Output 'No packages installed and no services started or switched.'
}
finally { Pop-Location }
