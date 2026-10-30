param(
    [ValidateSet("dev", "preview", "build")]
    [string]$Mode = "dev"
)

$ErrorActionPreference = "Stop"

$project = "C:\Users\elshad.asgarov\Desktop\DWH\dwh"
$frontend = Join-Path $project "frontend"

Set-Location $frontend

if ($Mode -eq "dev") {
    Write-Host "=== FRONTEND DEV SERVER ==="
    npm run dev -- --host 127.0.0.1
    exit $LASTEXITCODE
}

if ($Mode -eq "build") {
    Write-Host "=== FRONTEND BUILD ==="
    npm run build
    exit $LASTEXITCODE
}

if ($Mode -eq "preview") {
    Write-Host "=== FRONTEND BUILD ==="

    npm run build

    if ($LASTEXITCODE -ne 0) {
        Write-Host "STOP: frontend build failed"
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "=== FRONTEND PREVIEW SERVER ==="

    npm run preview -- --host 127.0.0.1
    exit $LASTEXITCODE
}