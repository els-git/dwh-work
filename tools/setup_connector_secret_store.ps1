# I03: one-time server settings for source credentials (synthetic, decision V2).
# Adds DWH_SECRET_STORE_DIR, DWH_CONNECTOR_MASTER_KEY_ID and a new random
# DWH_CONNECTOR_MASTER_KEY_B64 to the project .env. The key is never printed.
# Refuses to run if any of these settings already exists in .env.
# Operational master-key management (ACL, backup, rotation) is I02-03, not here.

param(
    [ValidatePattern('^[A-Za-z0-9_.-]{1,64}$')]
    [string]$KeyId = "K2026_01"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root ".env"
$storeDir = Join-Path $env:LOCALAPPDATA "DWH\secret_store"
$keyId = $KeyId

if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    Write-Host "SETUP_RESULT: STOP (.env not found: $envPath)"
    exit 1
}

$text = [IO.File]::ReadAllText($envPath)
foreach ($name in @("DWH_SECRET_STORE_DIR", "DWH_CONNECTOR_MASTER_KEY_ID", "DWH_CONNECTOR_MASTER_KEY_B64")) {
    if ($text -match "(?m)^\s*$name\s*=") {
        Write-Host "SETUP_RESULT: STOP ($name already present in .env; nothing changed)"
        exit 1
    }
}

New-Item -ItemType Directory -Force -Path $storeDir | Out-Null

$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$key = [Convert]::ToBase64String($bytes)
[Array]::Clear($bytes, 0, $bytes.Length)

$newline = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
$prefix = if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) { $newline } else { "" }
$lines = $prefix +
    "# I03 source credentials (synthetic master key, decision V2)" + $newline +
    "DWH_SECRET_STORE_DIR=$storeDir" + $newline +
    "DWH_CONNECTOR_MASTER_KEY_ID=$keyId" + $newline +
    "DWH_CONNECTOR_MASTER_KEY_B64=$key" + $newline
[IO.File]::AppendAllText($envPath, $lines, (New-Object Text.UTF8Encoding($false)))
$key = $null

Write-Host "DWH_SECRET_STORE_DIR=$storeDir"
Write-Host "DWH_CONNECTOR_MASTER_KEY_ID=$keyId"
Write-Host "DWH_CONNECTOR_MASTER_KEY_B64=<hidden, 32 bytes>"
Write-Host "SETUP_RESULT: PASS (restart the backend to apply)"
