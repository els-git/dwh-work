# Run one SQL file from this repository against dwh in READ-ONLY mode.
# Uses the project launcher's .env loader, get_api_config() and connect_readonly()
# (role dwh_app); stops unless SHOW transaction_read_only = on; always rollback.
# Output goes to the console and to runtime\temp\readonly_<name>_<time>.txt.
# Usage (Windows PowerShell 5.1), from any folder:
#   powershell -ExecutionPolicy Bypass -File "C:\Users\elshad.asgarov\Desktop\DWH\dwh\database\tools\run_readonly_sql.ps1" -SqlFile "database\diagnostics\<file>.sql"
param([Parameter(Mandatory = $true)][string]$SqlFile)
$ErrorActionPreference = 'Stop'
$Root = 'C:\Users\elshad.asgarov\Desktop\DWH\dwh'
$python = 'C:\Python312\python.exe'
Set-Location $Root
$sqlPath = Join-Path $Root $SqlFile
if (-not (Test-Path -LiteralPath $sqlPath)) { throw "SQL file not found: $sqlPath" }
$name = [IO.Path]::GetFileNameWithoutExtension($sqlPath)
$report = Join-Path $Root ("runtime\temp\readonly_{0}_{1}.txt" -f $name, (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path (Split-Path $report) | Out-Null
Write-Host "SQL file: $sqlPath"
Write-Host "SHA256:   $((Get-FileHash -LiteralPath $sqlPath -Algorithm SHA256).Hash)"
$env:DWH_READONLY_SQL = $sqlPath
$env:DWH_READONLY_REPORT = $report
@'
import importlib.util, os
from pathlib import Path

spec = importlib.util.spec_from_file_location("launcher", Path("tools/run_local_dwh_threaded.py").resolve())
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.load_env()
launcher.configure_python()

from dwh_api.config import get_api_config
from dwh_api.db import connect_readonly

text = Path(os.environ["DWH_READONLY_SQL"]).read_text(encoding="utf-8-sig")  # files saved by Set-Content -Encoding UTF8 carry a BOM
code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("--"))
statements = [s for s in code.split(";") if s.strip()]
out = []

def emit(line):
    print(line)
    out.append(line)

conn = connect_readonly(get_api_config())
try:
    with conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        ro = list(cur.fetchone().values())[0]
        emit("SHOW transaction_read_only: " + str(ro))
        if ro != "on":
            raise SystemExit("STOP: connection is not read-only")
        for st in statements:
            cur.execute(st)
            for row in cur.fetchall():
                emit(" | ".join("" if v is None else str(v) for v in row.values()))
finally:
    conn.rollback()
    conn.close()
emit("READONLY_SQL_FINISHED")
Path(os.environ["DWH_READONLY_REPORT"]).write_text("\n".join(out) + "\n", encoding="utf-8")
'@ | & $python -
if ($LASTEXITCODE -ne 0) { throw "FAILED: python exit code $LASTEXITCODE" }
Write-Host "Report: $report"
