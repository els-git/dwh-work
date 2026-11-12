# I16 step 7: export of the database dwh from the PC for the server.
#   & "C:\Users\elshad.asgarov\Desktop\DWH\dwh\tools\export_dwh_for_server.ps1"
# The PC database is the Windows service postgresql-x64-17 on 127.0.0.1:5433 (checked 12.11.2026),
# the tools are the standard ones from C:\Program Files\PostgreSQL\17\bin, as in the E2 scripts.
# The admin user comes from .env (DWH_DB_ADMIN only); its password is asked and kept only in this process.
# Reads only (pg_dump, pg_dumpall, read-only psql). Result: runtime\server_export\dwh_export_<time>\ with
#   roles.sql   roles and memberships WITHOUT passwords (pg_dumpall --roles-only --no-role-passwords)
#   dwh.dump    the database (pg_dump -Fc, checked with pg_restore --list)
#   info.txt    owner, encoding, collation, server version
#   counts.txt  rows of every table and objects per schema (compared on the server)
#   SHA256SUMS  sha256sum format, checked on the server
# Contains business data: copy it only to the server, delete it from the PC after the transfer.

# Native command errors are checked through $LASTEXITCODE (PowerShell 5.1 turns stderr into errors under Stop).
$ErrorActionPreference = "Continue"

$Root   = "C:\Users\elshad.asgarov\Desktop\DWH\dwh"
$Bin    = "C:\Program Files\PostgreSQL\17\bin"
$DbHost = "127.0.0.1"
$DbPort = "5433"
$Db     = "dwh"
$stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
$out    = Join-Path $Root "runtime\server_export\dwh_export_$stamp"

function Stop-Export([string]$Reason) {
    Remove-Item Env:PGPASSWORD, Env:PGOPTIONS -ErrorAction SilentlyContinue
    Write-Host "EXPORT_RESULT: FAIL ($Reason)"
    exit 1
}

function Invoke-Pg([string]$What, [string]$Exe, [string[]]$Arguments) {
    $lines = @(& (Join-Path $Bin $Exe) @Arguments 2>&1 | ForEach-Object { "$_" })
    if ($LASTEXITCODE -ne 0) {
        $lines | Select-Object -Last 5 | ForEach-Object { Write-Host "  $_" }
        Stop-Export "$What, exit code $LASTEXITCODE"
    }
}

foreach ($exe in "psql.exe", "pg_dump.exe", "pg_dumpall.exe", "pg_restore.exe") {
    if (-not (Test-Path -LiteralPath (Join-Path $Bin $exe))) { Stop-Export "$exe not found in $Bin" }
}
$counts = Join-Path $Root "tools\server\table_counts.sql"
if (-not (Test-Path -LiteralPath $counts)) { Stop-Export "$counts not found (run git pull)" }

$admin = ""
$envFile = Join-Path $Root ".env"
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*DWH_DB_ADMIN\s*=\s*(.+?)\s*$') { $admin = $Matches[1].Trim('"', "'") }
    }
}
if (-not $admin) { $admin = Read-Host "PostgreSQL admin user" }
Write-Host "server $DbHost`:$DbPort, database $Db, user $admin"

# Ctrl+V does not paste into this prompt in Windows PowerShell 5.1: type the password or paste with a right click.
Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
for ($attempt = 1; $attempt -le 3 -and -not $env:PGPASSWORD; $attempt++) {
    $secure = Read-Host "Password for $admin (type or right-click paste)" -AsSecureString
    if ($secure.Length -eq 0) { Write-Host "  empty password (attempt $attempt of 3)"; continue }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
if (-not $env:PGPASSWORD) { Stop-Export "no password entered" }

New-Item -ItemType Directory -Force -Path $out | Out-Null
$conn = @("-h", $DbHost, "-p", $DbPort, "-U", $admin, "-w")
$env:PGOPTIONS = "-c default_transaction_read_only=on"
$infoSql = "SELECT 'owner=' || pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database() " +
           "UNION ALL SELECT 'encoding=' || pg_encoding_to_char(encoding) FROM pg_database WHERE datname = current_database() " +
           "UNION ALL SELECT 'collate=' || datcollate FROM pg_database WHERE datname = current_database() " +
           "UNION ALL SELECT 'ctype=' || datctype FROM pg_database WHERE datname = current_database() " +
           "UNION ALL SELECT 'server_version_num=' || current_setting('server_version_num')"
$psql = $conn + @("-d", $Db, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1")
Invoke-Pg "database facts" "psql.exe" ($psql + @("-c", $infoSql, "-o", (Join-Path $out "info.txt")))
Invoke-Pg "roles" "pg_dumpall.exe" ($conn + @("-l", $Db, "--roles-only", "--no-role-passwords", "-f", (Join-Path $out "roles.sql")))
Invoke-Pg "database copy" "pg_dump.exe" ($conn + @("-d", $Db, "-Fc", "-f", (Join-Path $out "dwh.dump")))
Invoke-Pg "copy check" "pg_restore.exe" @("--list", "-f", (Join-Path $env:TEMP "dwh_export_list_$stamp.txt"), (Join-Path $out "dwh.dump"))
Remove-Item (Join-Path $env:TEMP "dwh_export_list_$stamp.txt") -ErrorAction SilentlyContinue
Invoke-Pg "counts" "psql.exe" ($psql + @("-f", $counts, "-o", (Join-Path $out "counts.txt")))
Remove-Item Env:PGPASSWORD, Env:PGOPTIONS -ErrorAction SilentlyContinue

$lines = foreach ($name in @("roles.sql", "dwh.dump", "info.txt", "counts.txt")) {
    $file = Join-Path $out $name
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { Stop-Export "$name missing" }
    "{0}  {1}" -f (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower(), $name
}
[IO.File]::WriteAllText((Join-Path $out "SHA256SUMS"), (($lines -join "`n") + "`n"))

$tables = (Select-String -LiteralPath (Join-Path $out "counts.txt") -Pattern "^rows\|").Count
$size = (Get-Item -LiteralPath (Join-Path $out "dwh.dump")).Length
Write-Host "export: $out"
Write-Host "database dwh: $tables tables, copy $size bytes"
Get-Content -LiteralPath (Join-Path $out "info.txt")
Write-Host "EXPORT_RESULT: PASS"
