# Sync DWH project documents from git (els-git/dwh-work) to this PC.
# Each file is embedded and SHA256-verified. A local file is replaced only when
# its current SHA256 is a version known from git history (or the file is
# absent); otherwise NOTHING is written and the script stops, so local edits
# are never overwritten. Replaced files are backed up first.
# Run in Windows PowerShell 5.1: powershell -ExecutionPolicy Bypass -File <this file>
$ErrorActionPreference = 'Stop'

$Root   = 'C:\Users\elshad.asgarov\Desktop\DWH\dwh'
$Stamp  = Get-Date -Format 'yyyyMMdd_HHmmss'
$Backup = Join-Path $Root "runtime\backup\docs_sync_$Stamp"

$Files = @(
@@FILES@@
)

if (-not (Test-Path -LiteralPath $Root)) { throw "STOP: project folder not found: $Root" }

# 1. Decide for every file before writing anything.
$plan = @()
foreach ($f in $Files) {
    $bytes = [Convert]::FromBase64String($f.Data)
    $sha = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($bytes)).Replace('-', '')
    if ($sha -ne $f.Sha) { throw "STOP: embedded copy of $($f.Path) is damaged" }
    $target = Join-Path $Root $f.Path
    if (-not (Test-Path -LiteralPath $target)) {
        $action = 'CREATE'
    } else {
        $current = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
        if ($current -eq $f.Sha) { $action = 'CURRENT' }
        elseif ($f.Known -contains $current) { $action = 'REPLACE' }
        else { $action = 'UNKNOWN'; $f.Current = $current }
    }
    $plan += [pscustomobject]@{ File = $f; Target = $target; Bytes = $bytes; Action = $action }
}
foreach ($p in $plan) { Write-Host ("{0,-8} {1}" -f $p.Action, $p.File.Path) }
$unknown = @($plan | Where-Object { $_.Action -eq 'UNKNOWN' })
if ($unknown.Count -gt 0) {
    foreach ($u in $unknown) { Write-Host "  local SHA256 $($u.File.Current) is not a known git version: $($u.File.Path)" }
    throw 'STOP: local changes found; nothing was written. Send these files for comparison.'
}

# 2. Back up and write.
foreach ($p in $plan | Where-Object { $_.Action -in 'CREATE', 'REPLACE' }) {
    if ($p.Action -eq 'REPLACE') {
        $copy = Join-Path $Backup $p.File.Path
        New-Item -ItemType Directory -Force -Path (Split-Path $copy) | Out-Null
        Copy-Item -LiteralPath $p.Target -Destination $copy
    } else {
        New-Item -ItemType Directory -Force -Path (Split-Path $p.Target) | Out-Null
    }
    [IO.File]::WriteAllBytes($p.Target, $p.Bytes)
    $check = (Get-FileHash -LiteralPath $p.Target -Algorithm SHA256).Hash
    if ($check -ne $p.File.Sha) { throw "STOP: verification failed for $($p.File.Path); backup in $Backup" }
}

foreach ($p in $plan) { Write-Host ("SHA256 {0} {1}" -f $p.File.Sha, $p.File.Path) }
if (Test-Path -LiteralPath $Backup) { Write-Host "Previous versions: $Backup" }
Write-Host 'DOCS_SYNC_RESULT: PASS'
