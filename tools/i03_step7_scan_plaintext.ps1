# I03 step 7: make sure the synthetic credential values used in the UI test
# are not stored in plain text anywhere: runtime logs/temp files of the project
# and the encrypted secret store folder. Read-only; prints only file names and
# counts, never file contents. Reads DWH_SECRET_STORE_DIR from .env (only that line).
param(
    [string[]]$Values = @("synthetic-A-0411", "synthetic-B-0411")
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root ".env"
$storeLine = Get-Content -LiteralPath $envPath -Encoding UTF8 | Where-Object { $_ -match '^\s*DWH_SECRET_STORE_DIR\s*=' } | Select-Object -First 1
if (-not $storeLine) { Write-Host "SCAN_RESULT: STOP (DWH_SECRET_STORE_DIR not in .env)"; exit 1 }
$storeDir = ($storeLine -split '=', 2)[1].Trim()

$folders = @((Join-Path $root "runtime"), $storeDir, (Join-Path $root "backend"), (Join-Path $root "frontend\dist"))
$patterns = @()
foreach ($v in $Values) {
    $patterns += , ([Text.Encoding]::UTF8.GetBytes($v))
    $patterns += , ([Text.Encoding]::Unicode.GetBytes($v))
}

function Test-Contains([byte[]]$data, [byte[]]$pattern) {
    $limit = $data.Length - $pattern.Length
    $first = $pattern[0]
    for ($i = [Array]::IndexOf($data, $first); $i -ge 0 -and $i -le $limit; $i = [Array]::IndexOf($data, $first, $i + 1)) {
        $match = $true
        for ($k = 1; $k -lt $pattern.Length; $k++) {
            if ($data[$i + $k] -ne $pattern[$k]) { $match = $false; break }
        }
        if ($match) { return $true }
    }
    return $false
}

$scanned = 0
$found = @()
foreach ($folder in $folders) {
    if (-not (Test-Path -LiteralPath $folder)) { Write-Host "skip (absent): $folder"; continue }
    $files = Get-ChildItem -LiteralPath $folder -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -le 100MB -and $_.FullName -notmatch '\\(node_modules|packages|__pycache__)\\' }
    foreach ($file in $files) {
        $scanned++
        $data = [IO.File]::ReadAllBytes($file.FullName)
        foreach ($pattern in $patterns) {
            if (Test-Contains $data $pattern) { $found += $file.FullName; break }
        }
    }
}

$bins = @(Get-ChildItem -LiteralPath $storeDir -Filter *.bin -File -ErrorAction SilentlyContinue).Count
Write-Host "Secret store folder: $storeDir; encrypted files (*.bin): $bins"
Write-Host "Files scanned: $scanned"
if ($found.Count -gt 0) {
    $found | Sort-Object -Unique | ForEach-Object { Write-Host "PLAINTEXT_FOUND: $_" }
    Write-Host "SCAN_RESULT: FAIL"
    exit 1
}
Write-Host "SCAN_RESULT: PASS (synthetic values not found in plain text)"
