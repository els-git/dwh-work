# I16 step 7: export of the database dwh from the PC (Docker container dwh-postgres) for the server.
#   & "C:\Users\elshad.asgarov\Desktop\DWH\dwh\tools\export_dwh_for_server.ps1"
# Everything runs inside the container (its own pg_dump 17): no passwords are asked or written.
# Result: runtime\server_export\dwh_export_<time>\ with
#   roles.sql   roles and memberships WITHOUT passwords (pg_dumpall --roles-only --no-role-passwords)
#   dwh.dump    the database (pg_dump -Fc, checked with pg_restore --list)
#   info.txt    owner, encoding, collation, server version
#   counts.txt  rows of every table and objects per schema (compared on the server)
#   SHA256SUMS  sha256sum format, checked on the server
# Contains business data: copy it only to the server, delete it from the PC after the transfer.
param(
    [string]$Container = "dwh-postgres"
)

# Native command errors are checked through $LASTEXITCODE (PowerShell 5.1 turns stderr into errors under Stop).
$ErrorActionPreference = "Continue"

$project = "C:\Users\elshad.asgarov\Desktop\DWH\dwh"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outRoot = Join-Path $project "runtime\server_export"
$out = Join-Path $outRoot "dwh_export_$stamp"

function Invoke-Checked {
    param([string]$What, [string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "EXPORT_RESULT: FAIL ($What, exit code $LASTEXITCODE)"
        exit 1
    }
}

$running = (& docker inspect -f "{{.State.Running}}" $Container) 2>$null
if ($running -ne "true") {
    Write-Host "EXPORT_RESULT: FAIL (container $Container is not running)"
    exit 1
}
$admin = (& docker exec $Container printenv POSTGRES_USER).Trim()
$db = (& docker exec $Container printenv POSTGRES_DB).Trim()
if ($db -ne "dwh") {
    Write-Host "EXPORT_RESULT: FAIL (database of the container is '$db', expected dwh)"
    exit 1
}

Invoke-Checked "prepare" @("exec", $Container, "sh", "-c", "rm -rf /tmp/dwhexp && mkdir /tmp/dwhexp")
Invoke-Checked "copy queries" @("cp", (Join-Path $project "tools\server\table_counts.sql"), "${Container}:/tmp/dwhexp/table_counts.sql")
Invoke-Checked "copy queries" @("cp", (Join-Path $project "tools\server\dwh_export_queries.sql"), "${Container}:/tmp/dwhexp/dwh_export_queries.sql")
Invoke-Checked "roles" @("exec", $Container, "pg_dumpall", "-U", $admin, "--roles-only", "--no-role-passwords", "-f", "/tmp/dwhexp/roles.sql")
Invoke-Checked "database copy" @("exec", $Container, "pg_dump", "-U", $admin, "-Fc", "-f", "/tmp/dwhexp/dwh.dump", $db)
Invoke-Checked "copy check" @("exec", $Container, "sh", "-c", "pg_restore --list /tmp/dwhexp/dwh.dump > /dev/null")
Invoke-Checked "counts" @("exec", $Container, "psql", "-U", $admin, "-d", $db, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", "/tmp/dwhexp/dwh_export_queries.sql")
Invoke-Checked "remove helpers" @("exec", $Container, "rm", "-f", "/tmp/dwhexp/table_counts.sql", "/tmp/dwhexp/dwh_export_queries.sql")

New-Item -ItemType Directory -Force -Path $outRoot | Out-Null
Invoke-Checked "copy out" @("cp", "${Container}:/tmp/dwhexp", $out)
Invoke-Checked "clean container" @("exec", $Container, "rm", "-rf", "/tmp/dwhexp")

$lines = foreach ($name in @("roles.sql", "dwh.dump", "info.txt", "counts.txt")) {
    $file = Join-Path $out $name
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        Write-Host "EXPORT_RESULT: FAIL ($name missing)"
        exit 1
    }
    "{0}  {1}" -f (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower(), $name
}
[IO.File]::WriteAllText((Join-Path $out "SHA256SUMS"), (($lines -join "`n") + "`n"))

$tables = (Select-String -LiteralPath (Join-Path $out "counts.txt") -Pattern "^rows\|").Count
$size = (Get-Item -LiteralPath (Join-Path $out "dwh.dump")).Length
Write-Host "export: $out"
Write-Host "database dwh: $tables tables, copy $size bytes"
Get-Content -LiteralPath (Join-Path $out "info.txt")
Write-Host "EXPORT_RESULT: PASS"
