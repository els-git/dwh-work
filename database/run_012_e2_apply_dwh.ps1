# I06-05 package 012: E2 - apply 012_object_ods_config to the WORKING dwh.
# ODS configuration of objects (version line, white list of transformations), ODS step of runs.
# Run only after E1 PASS and the user's permission.
# Steps: verify dwh is in the 011 state (check 011 8/8) -> fresh backup
# (pg_dump -Fc + globals without role passwords) -> confirmation by typing
# APPLY -> 012 in ONE transaction (lock_timeout 10s) -> check 012 10/10, check 011
# 8/8, check 010 9/9 -> row counts unchanged. Any failure = STOP; a failed apply rolls back completely.
# Rollback after success: database\migrations\012_object_ods_config.down.sql
# The dwh_admin password is asked interactively and kept only in this process.
# Run in Windows PowerShell 5.1: powershell -ExecutionPolicy Bypass -File <this file>
$ErrorActionPreference = 'Stop'

$Root   = 'C:\Users\elshad.asgarov\Desktop\DWH\dwh'
$Bin    = 'C:\Program Files\PostgreSQL\17\bin'
$DbHost = '127.0.0.1'
$DbPort = '5433'
$Source = 'dwh'
$Stamp  = Get-Date -Format 'yyyyMMdd_HHmmss'
$Work   = Join-Path $Root "runtime\temp\I06_05_012_E2_$Stamp"
$Backup = Join-Path $Root 'runtime\backup'
$Report = Join-Path $Work 'E2_report.txt'

# Files come from the repository (after git pull); SHA256 must match.
$Files = [ordered]@{
    '010_object_run_check.sql' = @{ Sha = '665016109541F4A3DBEBAB887B1B599550673383CCD912E5AFB64DAA4E8E8F03'; Path = (Join-Path $Root 'database\checks\010_object_run_check.sql') }
    '011_object_load_strategy_check.sql' = @{ Sha = '01FE2C0FD33099F7030808D52CEDAC7859E29A7ADC7A771441857818062AC98B'; Path = (Join-Path $Root 'database\checks\011_object_load_strategy_check.sql') }
    '012_object_ods_config.sql' = @{ Sha = 'B8427E29605DA039EB50F035560EAFD3159E38AC3966EB2BD324B62152635A47'; Path = (Join-Path $Root 'database\migrations\012_object_ods_config.sql') }
    '012_object_ods_config.down.sql' = @{ Sha = 'BCE8968C94F252BA4F47168A8F1A3EB53CB65FC641BA73B558DFC1F3D1ACAD19'; Path = (Join-Path $Root 'database\migrations\012_object_ods_config.down.sql') }
    '012_object_ods_config_check.sql' = @{ Sha = '6CD9D471940795AC0485B82B099B1E6D773E093A29E63C8E1E02C6C5AA79C88B'; Path = (Join-Path $Root 'database\checks\012_object_ods_config_check.sql') }
    'catalog_snapshot.sql' = @{ Sha = '9F43AB25B2595E7816028D05380C7F7EDF07D64AAFF8206D36595C64765D614F'; Path = (Join-Path $Root 'database\checks\catalog_snapshot.sql') }
    'secret_store_row_counts.sql' = @{ Sha = 'BF3199763FC8C9522346B5F6BE5EEC48694536252D352C7C9E3C90078D917A36'; Path = (Join-Path $Root 'database\checks\secret_store_row_counts.sql') }
}

function Write-Log([string]$Text) {
    Write-Host $Text
    Add-Content -LiteralPath $Report -Value $Text -Encoding UTF8
}

function Stop-E2([string]$Reason) {
    Write-Log "STOP: $Reason"
    Write-Log 'E2_RESULT: STOP'
    throw "STOP: $Reason"
}

function Invoke-Tool([string]$Exe, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $lines = @(& (Join-Path $Bin $Exe) @Arguments 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    foreach ($line in $lines) { Write-Log "  $line" }
    return [pscustomobject]@{ Code = $code; Lines = $lines }
}

function Invoke-Psql([string]$Database, [string[]]$Arguments, [switch]$ReadOnly) {
    if ($ReadOnly) { $env:PGOPTIONS = '-c default_transaction_read_only=on' }
    try {
        $base = @('-h', $DbHost, '-p', $DbPort, '-U', $script:Admin, '-d', $Database, '-w', '-X', '-At', '-v', 'ON_ERROR_STOP=1')
        return Invoke-Tool 'psql.exe' ($base + $Arguments)
    } finally {
        if ($ReadOnly) { Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue }
    }
}

function Assert-Ok($Result, [string]$Step) {
    if ($Result.Code -ne 0) { Stop-E2 "$Step failed (exit code $($Result.Code))" }
}

function Get-Pass($Result) { @($Result.Lines | Where-Object { $_ -match '\|PASS$' }).Count }
function Get-Fail($Result) { @($Result.Lines | Where-Object { $_ -match '\|FAIL$' }).Count }

function Invoke-Check([string]$Database, [string]$File, [int]$Expected, [string]$Step) {
    Write-Log "== $Step"
    $r = Invoke-Psql $Database @('-f', $Files[$File].Path) -ReadOnly
    Assert-Ok $r $Step
    $pass = Get-Pass $r
    $fail = Get-Fail $r
    Write-Log "  RESULT: PASS=$pass/$Expected FAIL=$fail"
    if ($pass -ne $Expected -or $fail -ne 0) { Stop-E2 "$Step expected $Expected PASS" }
}

function Get-Snapshot([string]$Database) {
    $r = Invoke-Psql $Database @('-f', $Files['catalog_snapshot.sql'].Path) -ReadOnly
    Assert-Ok $r "catalog snapshot of $Database"
    return ($r.Lines | Where-Object { $_ -match '^\d+:[0-9a-f]{32}$' } | Select-Object -First 1)
}

function Get-RowCounts([string]$Database) {
    $r = Invoke-Psql $Database @('-f', $Files['secret_store_row_counts.sql'].Path) -ReadOnly
    Assert-Ok $r "row counts of $Database"
    return @($r.Lines | Where-Object { $_ -match '^[a-z_:A-Z]+=\d+$' })
}

New-Item -ItemType Directory -Force -Path $Work, $Backup | Out-Null
Write-Log "I05 008 E2 apply to dwh, started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Log "Work folder: $Work"

try {
    # 0. Files and tools.
    foreach ($name in $Files.Keys) {
        $path = $Files[$name].Path
        if (-not (Test-Path -LiteralPath $path)) { Stop-E2 "file not found: $path (run git pull)" }
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        if ($hash -ne $Files[$name].Sha) { Stop-E2 "SHA256 mismatch for $name" }
        Write-Log "FILE $name SHA256 $hash"
    }
    foreach ($exe in 'psql.exe', 'pg_dump.exe', 'pg_dumpall.exe', 'pg_restore.exe') {
        if (-not (Test-Path -LiteralPath (Join-Path $Bin $exe))) { Stop-E2 "$exe not found in $Bin" }
    }

    # 1. Admin identity from .env (only DWH_DB_ADMIN and DWH_DB_PORT are read).
    $envFile = Join-Path $Root '.env'
    $script:Admin = ''
    if (Test-Path -LiteralPath $envFile) {
        foreach ($line in Get-Content -LiteralPath $envFile) {
            if ($line -match '^\s*DWH_DB_ADMIN\s*=\s*(.+?)\s*$') { $script:Admin = $Matches[1].Trim('"', "'") }
            if ($line -match '^\s*DWH_DB_PORT\s*=\s*(.+?)\s*$' -and $Matches[1].Trim('"', "'") -ne $DbPort) {
                Stop-E2 "DWH_DB_PORT in .env is not $DbPort"
            }
        }
    }
    if (-not $script:Admin) { $script:Admin = Read-Host 'PostgreSQL admin user' }
    Write-Log "Admin user: $($script:Admin); server $DbHost`:$DbPort"
    # Ctrl+V does not paste into this prompt in Windows PowerShell 5.1: type the
    # password or paste with a right click. An empty value is rejected.
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    for ($attempt = 1; $attempt -le 3 -and -not $env:PGPASSWORD; $attempt++) {
        $secure = Read-Host "Password for $($script:Admin) (type or right-click paste)" -AsSecureString
        if ($secure.Length -eq 0) { Write-Log "  empty password entered (attempt $attempt of 3)"; continue }
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    }
    if (-not $env:PGPASSWORD) { Stop-E2 'no password entered' }

    # 2. Connection.
    Write-Log '== 2 connection'
    $r = Invoke-Psql $Source @('-c', "select current_user || ' | ' || current_setting('server_version') || ' | ' || current_database()") -ReadOnly
    Assert-Ok $r 'connection'
    if (-not ($r.Lines -match '\| 17\.')) { Stop-E2 'server is not PostgreSQL 17' }
    if (-not ($r.Lines -match '\| dwh$')) { Stop-E2 'not connected to dwh' }

    # 3. dwh must be exactly in the 011 state.
    Invoke-Check $Source '011_object_load_strategy_check.sql' 8 '3 dwh: 011 check (read-only)'
    $before = Get-Snapshot $Source
    $rowsBefore = Get-RowCounts $Source
    Write-Log "  dwh snapshot (011): $before"
    Write-Log "  rows: $($rowsBefore -join ' ')"

    # 4. Backup.
    Write-Log '== 4 backup'
    $dump = Join-Path $Backup "dwh_I06_05_E2_before_012_$Stamp.dump"
    $globals = Join-Path $Backup "dwh_I06_05_E2_before_012_${Stamp}_globals_no_passwords.sql"
    Assert-Ok (Invoke-Tool 'pg_dump.exe' @('-h', $DbHost, '-p', $DbPort, '-U', $script:Admin, '-w', '-d', $Source, '-Fc', '-f', $dump)) 'pg_dump'
    Assert-Ok (Invoke-Tool 'pg_dumpall.exe' @('-h', $DbHost, '-p', $DbPort, '-U', $script:Admin, '-w', '-l', $Source, '--globals-only', '--no-role-passwords', '-f', $globals)) 'pg_dumpall'
    $list = Invoke-Tool 'pg_restore.exe' @('--list', $dump)
    if ($list.Code -ne 0) { Stop-E2 'backup is not readable by pg_restore --list' }
    Write-Log "  dump entries: $(@($list.Lines | Where-Object { $_ -match '^\d+;' }).Count)"
    foreach ($f in $dump, $globals) {
        Write-Log "  BACKUP $f SHA256 $((Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash) bytes $((Get-Item -LiteralPath $f).Length)"
    }
    if ((Get-Snapshot $Source) -ne $before) { Stop-E2 'dwh catalog changed during backup; nothing was applied' }

    # 5. Explicit confirmation.
    $answer = Read-Host 'Type APPLY to apply 012 to the working dwh, anything else to stop'
    if ($answer -cne 'APPLY') { Stop-E2 'not confirmed; nothing was applied' }
    Write-Log '  confirmed by user: APPLY'

    # 6. Apply in one transaction; do not wait for locks indefinitely.
    Write-Log '== 6 apply 012 to dwh'
    $env:PGOPTIONS = '-c lock_timeout=10s'
    try {
        $r = Invoke-Psql $Source @('-1', '-f', $Files['012_object_ods_config.sql'].Path)
    } finally {
        Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue
    }
    if ($r.Code -ne 0) {
        $after = Get-Snapshot $Source
        if ($after -eq $before) { Write-Log '  transaction rolled back: dwh catalog unchanged' }
        Stop-E2 "apply 012 failed (exit code $($r.Code))"
    }

    # 7. Verify.
    Invoke-Check $Source '012_object_ods_config_check.sql' 10 '7 dwh: 012 check (read-only)'
    Invoke-Check $Source '011_object_load_strategy_check.sql' 8 '7 dwh: 011 check (read-only)'
    Invoke-Check $Source '010_object_run_check.sql' 9 '7 dwh: 010 check (read-only)'
    $after = Get-Snapshot $Source
    $rowsAfter = Get-RowCounts $Source
    Write-Log "  dwh snapshot (012): $after"
    Write-Log "  rows: $($rowsAfter -join ' ')"
    if ($after -eq $before) { Stop-E2 'catalog snapshot did not change after apply' }
    if (($rowsAfter -join ' ') -ne ($rowsBefore -join ' ')) { Stop-E2 'row counts changed by the migration' }
    Write-Log "Rollback file (only if needed, by decision): $($Files['012_object_ods_config.down.sql'].Path)"
    Write-Log 'E2_RESULT: PASS'
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue
    Write-Host "Report: $Report"
}
