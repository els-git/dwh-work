param(
    [ValidateSet("Check", "Run")]
    [string]$Mode = "Check"
)

$ErrorActionPreference = "Stop"

$project = "C:\Users\elshad.asgarov\Desktop\DWH\dwh"
$python = "C:\Python312\python.exe"

$entry = "$project\tools\run_local_dwh_threaded.py"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python executable not found"
}

if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
    throw "Local DWH Python coordinator not found"
}

Push-Location $project

try {
    # Exactly one Python process.
    # Uvicorn runs on the main thread.
    # Dispatcher runs on a second thread.
    # No Start-Process or subprocess is used.
    & $python -u -c "import runpy,sys; sys.argv=[r'$entry', '$Mode']; runpy.run_path(r'$entry', run_name='__main__')"

    if ($LASTEXITCODE -ne 0) {
        throw "Local DWH exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}