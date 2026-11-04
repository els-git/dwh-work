# I02-05: SecretStore master keys (Windows DPAPI files) - create, back up,
# restore, verify and activate. Never prints key material or passphrases.
#
#   New      -KeyId K2026_03 -BackupDir "E:\dwh_key_backup"   new key + backup file
#   Restore  -BackupFile "E:\dwh_key_backup\K2026_03.dwhkey"  rebuild key from backup
#   Verify   -KeyId K2026_03 [-BackupFile ...]                key opens; backup matches
#   Activate -KeyId K2026_03                                  .env: keyring + active key,
#                                                             old plain-text key removed
# New and Restore also restrict the keyring and secret store folders to the
# current Windows user, SYSTEM and Administrators. Restart the backend after Activate.
param(
    [Parameter(Mandatory = $true)][ValidateSet("New", "Restore", "Verify", "Activate")][string]$Action,
    [ValidatePattern('^[A-Za-z0-9_.-]{1,64}$')][string]$KeyId,
    [string]$BackupDir,
    [string]$BackupFile
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = "C:\Python312\python.exe"
$tool = Join-Path $PSScriptRoot "secret_key_tool.py"
$keyring = Join-Path $env:LOCALAPPDATA "DWH\keyring"
$store = Join-Path $env:LOCALAPPDATA "DWH\secret_store"

function Set-PrivateAcl([string]$path) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    # SIDs, so the command works on any Windows language: user, SYSTEM, Administrators.
    $output = & icacls.exe $path /inheritance:r /grant:r "*${user}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host $output; throw "ACL update failed: $path" }
    Write-Host "ACL: $path -> current user, SYSTEM, Administrators only"
}

if ($Action -in @("New", "Restore")) {
    Set-PrivateAcl $keyring
    Set-PrivateAcl $store
}

$arguments = @($tool, $Action.ToLower(), "--keyring", $keyring)
if ($KeyId) { $arguments += @("--key-id", $KeyId) }
if ($BackupDir) { $arguments += @("--backup-dir", $BackupDir) }
if ($BackupFile) { $arguments += @("--backup-file", $BackupFile) }

Push-Location $root
try {
    & $python -I @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}
