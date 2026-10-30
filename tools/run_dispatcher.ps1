param(
    [ValidateSet(
        "once",
        "loop"
    )]
    [string]$Mode = "once"
)

$ErrorActionPreference = "Stop"

$project = "C:\Users\elshad.asgarov\Desktop\DWH\dwh"
$python = "C:\Python312\python.exe"

$env:PYTHONPATH = "$project\etl\src;$project\etl\packages"
# Python timezone database for psycopg / ZoneInfo.
# Use PostgreSQL 17 TZif files; do not change Windows timezone.
$timezoneDir = "C:\Program Files\PostgreSQL\17\share\timezone"
$timezoneFile = Join-Path $timezoneDir "Asia\Baku"

if (-not (Test-Path -LiteralPath $timezoneFile -PathType Leaf)) {
    throw "PostgreSQL Asia/Baku timezone file not found: $timezoneFile"
}

$env:PYTHONTZPATH = $timezoneDir

$onceScript = "$project\etl\src\run_request_dispatcher.py"
$loopScript = "$project\etl\src\run_request_dispatcher_loop.py"

if ($Mode -eq "once") {
    $script = $onceScript
}
else {
    $script = $loopScript
}

if (-not (Test-Path $script)) {
    throw "Dispatcher script not found: $script"
}

& $python `
    -c "import runpy; runpy.run_path(r'$script', run_name='__main__')"

if ($LASTEXITCODE -ne 0) {
    throw "Integration dispatcher failed with exit code $LASTEXITCODE"
}