# I16: build a release package for the DWH server on this PC (plan I16, section 5).
# Run from Windows PowerShell 5.1:
#   & "C:\Users\elshad.asgarov\Desktop\DWH\dwh\tools\make_release.ps1" -Version 2026.11.1
# Needs: clean main equal to origin/main (git pull first), npm (frontend build), internet to PyPI (wheels).
# Result: runtime\releases\dwh_release_<Version>.zip and .sha256 - copy both to the server (SFTP).
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [string]$PythonVersion = "3.13",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$project = "C:\Users\elshad.asgarov\Desktop\DWH\dwh"
$python = "C:\Python312\python.exe"
$script = Join-Path $project "tools\release\make_release.py"

if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    Write-Host "STOP: make_release.py not found"
    exit 1
}

Set-Location $project
$arguments = @($script, "--version", $Version, "--python-version", $PythonVersion)
if ($SkipBuild) {
    $arguments += "--skip-build"
}
& $python @arguments
exit $LASTEXITCODE
