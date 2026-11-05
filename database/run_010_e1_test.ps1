# I06 package 010: E1 test of 010_object_run on a restored copy of dwh.
# Steps: backup dwh (pg_dump -Fc + globals without role passwords) -> restore
# into a NEW database dwh_i02_04_test on the same server -> preflight (check 009)
# -> apply 010 -> check 010 -> test (rolled back) -> down -> check 009 -> apply 010 again.
# An existing dwh_i02_04_test is dropped only after typing DROP.
# The working dwh is only read (backup, read-only checks). Any failure = STOP.
# The dwh_admin password is asked interactively and kept only in this process.
# Run in Windows PowerShell 5.1: powershell -ExecutionPolicy Bypass -File <this file>
$ErrorActionPreference = 'Stop'

$Root   = 'C:\Users\elshad.asgarov\Desktop\DWH\dwh'
$Bin    = 'C:\Program Files\PostgreSQL\17\bin'
$DbHost = '127.0.0.1'
$DbPort = '5433'
$Source = 'dwh'
$Target = 'dwh_i02_04_test'
$Stamp  = Get-Date -Format 'yyyyMMdd_HHmmss'
$Work   = Join-Path $Root "runtime\temp\I06_E1_010_$Stamp"
$Backup = Join-Path $Root 'runtime\backup'
$Report = Join-Path $Work 'E1_report.txt'

# Files come from the repository (after git pull); SHA256 must match.
$Files = [ordered]@{
    '009_target_operation_collector_asan_check.sql' = @{ Sha = '8784070DC92EBCF67F6A057B52770374CB67FF9AB7F303ADD743A7AC5CF68103'; Path = (Join-Path $Root 'database\checks\009_target_operation_collector_asan_check.sql') }
    '010_object_run.sql' = @{ Sha = '618B9E35F510CE09D15A98F834A9BF833ACA4774239CE55B1116274BF4B46EFD'; Path = (Join-Path $Root 'database\migrations\010_object_run.sql') }
    '010_object_run.down.sql' = @{ Sha = 'E21E007CF6F0FBFE308789545E5F1E813E1C13A3FD3BB61F462B82CDFA2B2B5E'; Path = (Join-Path $Root 'database\migrations\010_object_run.down.sql') }
    '010_object_run_check.sql' = @{ Sha = '665016109541F4A3DBEBAB887B1B599550673383CCD912E5AFB64DAA4E8E8F03'; Path = (Join-Path $Root 'database\checks\010_object_run_check.sql') }
    '010_object_run_test.sql' = @{ Sha = '55AA2C4C89C0AFA53BD928315B548FF4A814F66D1B0106B974D0AFA07DA8F029'; Path = (Join-Path $Root 'database\tests\010_object_run_test.sql') }
    'catalog_snapshot.sql' = @{ Sha = '9F43AB25B2595E7816028D05380C7F7EDF07D64AAFF8206D36595C64765D614F'; Path = (Join-Path $Root 'database\checks\catalog_snapshot.sql') }
    'secret_store_row_counts.sql' = @{ Sha = 'BF3199763FC8C9522346B5F6BE5EEC48694536252D352C7C9E3C90078D917A36'; Path = (Join-Path $Root 'database\checks\secret_store_row_counts.sql') }
}

function Write-Log([string]$Text) {
    Write-Host $Text
    Add-Content -LiteralPath $Report -Value $Text -Encoding UTF8
}

function Stop-E1([string]$Reason) {
    Write-Log "STOP: $Reason"
    Write-Log 'E1_RESULT: STOP'
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
    if ($Result.Code -ne 0) { Stop-E1 "$Step failed (exit code $($Result.Code))" }
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
    if ($pass -ne $Expected -or $fail -ne 0) { Stop-E1 "$Step expected $Expected PASS" }
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
Write-Log "I06 010 E1 test, started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Log "Work folder: $Work"

try {
    # 0. Files and tools.
    foreach ($name in $Files.Keys) {
        $path = $Files[$name].Path
        if (-not (Test-Path -LiteralPath $path)) { Stop-E1 "file not found: $path (run git pull)" }
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        if ($hash -ne $Files[$name].Sha) { Stop-E1 "SHA256 mismatch for $name" }
        Write-Log "FILE $name SHA256 $hash"
    }
    foreach ($exe in 'psql.exe', 'pg_dump.exe', 'pg_dumpall.exe', 'pg_restore.exe') {
        if (-not (Test-Path -LiteralPath (Join-Path $Bin $exe))) { Stop-E1 "$exe not found in $Bin" }
    }

    # 1. Admin identity from .env (only DWH_DB_ADMIN and DWH_DB_PORT are read).
    $envFile = Join-Path $Root '.env'
    $script:Admin = ''
    if (Test-Path -LiteralPath $envFile) {
        foreach ($line in Get-Content -LiteralPath $envFile) {
            if ($line -match '^\s*DWH_DB_ADMIN\s*=\s*(.+?)\s*$') { $script:Admin = $Matches[1].Trim('"', "'") }
            if ($line -match '^\s*DWH_DB_PORT\s*=\s*(.+?)\s*$' -and $Matches[1].Trim('"', "'") -ne $DbPort) {
                Stop-E1 "DWH_DB_PORT in .env is not $DbPort"
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
    if (-not $env:PGPASSWORD) { Stop-E1 'no password entered' }

    # 2. Connection and target absence.
    Write-Log '== 2 connection'
    $r = Invoke-Psql $Source @('-c', "select current_user || ' | ' || current_setting('server_version') || ' | ' || current_database()") -ReadOnly
    Assert-Ok $r 'connection'
    if (-not ($r.Lines -match '\| 17\.')) { Stop-E1 'server is not PostgreSQL 17' }
    $r = Invoke-Psql $Source @('-c', "select count(*) from pg_database where datname = '$Target'") -ReadOnly
    Assert-Ok $r 'target check'
    if (($r.Lines | Select-Object -Last 1) -ne '0') {
        # Only the fixed test copy name can be dropped, and only on explicit confirmation.
        if ($Target -ne 'dwh_i02_04_test') { Stop-E1 'unexpected target name' }
        Write-Log "Database $Target already exists (copy from a previous E1 run)."
        $answer = Read-Host "Type DROP to drop and recreate $Target, anything else to stop"
        if ($answer -cne 'DROP') { Stop-E1 "database $Target already exists; nothing was changed" }
        Write-Log "== 2b drop previous $Target (confirmed by user)"
        Assert-Ok (Invoke-Psql $Source @('-c', "DROP DATABASE $Target")) 'DROP DATABASE'
    }

    # 3. dwh before: read-only check and catalog snapshot.
    Invoke-Check $Source '009_target_operation_collector_asan_check.sql' 8 '3 dwh: 009 check (read-only)'
    $dwhBefore = Get-Snapshot $Source
    Write-Log "  dwh snapshot: $dwhBefore"

    # 4. Backup.
    Write-Log '== 4 backup'
    $dump = Join-Path $Backup "dwh_I06_E1_010_$Stamp.dump"
    $globals = Join-Path $Backup "dwh_I06_E1_010_${Stamp}_globals_no_passwords.sql"
    Assert-Ok (Invoke-Tool 'pg_dump.exe' @('-h', $DbHost, '-p', $DbPort, '-U', $script:Admin, '-w', '-d', $Source, '-Fc', '-f', $dump)) 'pg_dump'
    Assert-Ok (Invoke-Tool 'pg_dumpall.exe' @('-h', $DbHost, '-p', $DbPort, '-U', $script:Admin, '-w', '-l', $Source, '--globals-only', '--no-role-passwords', '-f', $globals)) 'pg_dumpall'
    $list = Invoke-Tool 'pg_restore.exe' @('--list', $dump)
    if ($list.Code -ne 0) { Stop-E1 'backup is not readable by pg_restore --list' }
    Write-Log "  dump entries: $(@($list.Lines | Where-Object { $_ -match '^\d+;' }).Count)"
    foreach ($f in $dump, $globals) {
        Write-Log "  BACKUP $f SHA256 $((Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash) bytes $((Get-Item -LiteralPath $f).Length)"
    }

    # 5. Restore into the new test database.
    Write-Log '== 5 restore'
    Assert-Ok (Invoke-Psql $Source @('-c', "CREATE DATABASE $Target TEMPLATE template0")) 'CREATE DATABASE'
    $r = Invoke-Psql $Source @('-c', "select string_agg(distinct pg_encoding_to_char(encoding), ',') from pg_database where datname in ('$Source', '$Target')") -ReadOnly
    Assert-Ok $r 'encoding check'
    if (($r.Lines | Select-Object -Last 1) -match ',') { Stop-E1 "encoding of $Target differs from $Source" }
    Assert-Ok (Invoke-Tool 'pg_restore.exe' @('-h', $DbHost, '-p', $DbPort, '-U', $script:Admin, '-w', '-d', $Target, '--exit-on-error', '--single-transaction', $dump)) 'pg_restore'

    # 6. Preflight on the copy.
    Invoke-Check $Target '009_target_operation_collector_asan_check.sql' 8 '6 copy: 009 check'
    $rowsBefore = Get-RowCounts $Target
    Write-Log "  rows: $($rowsBefore -join ' ')"
    $copyBASE = Get-Snapshot $Target
    Write-Log "  copy snapshot (009): $copyBASE"

    # 7-9. Apply 010, check, test.
    Write-Log '== 7 apply 010'
    Assert-Ok (Invoke-Psql $Target @('-1', '-f', $Files['010_object_run.sql'].Path)) 'apply 010'
    Invoke-Check $Target '010_object_run_check.sql' 9 '8 copy: 010 check'
    Invoke-Check $Target '009_target_operation_collector_asan_check.sql' 8 '8 copy: 009 check'
    $copyNEW = Get-Snapshot $Target
    Write-Log "  copy snapshot (010): $copyNEW"
    Write-Log '== 9 test 010 (rolled back)'
    $r = Invoke-Psql $Target @('-f', $Files['010_object_run_test.sql'].Path)
    Assert-Ok $r 'test 010'
    # Match the test labels, not the severity word: the server may localise NOTICE.
    $passed = @($r.Lines | Where-Object { $_ -match ':\s+PASS V\d{2}' }).Count
    Write-Log "  RESULT: PASS=$passed"
    if (-not ($r.Lines -contains 'ALL_TESTS_PASS') -or $passed -ne 17) { Stop-E1 'test 010 did not pass' }
    $rowsAfterTest = Get-RowCounts $Target
    if (($rowsAfterTest -join ' ') -ne ($rowsBefore -join ' ')) { Stop-E1 'row counts changed after the rolled-back test' }

    # 10-11. Down, check 009, apply again.
    Write-Log '== 10 down 010'
    Assert-Ok (Invoke-Psql $Target @('-f', $Files['010_object_run.down.sql'].Path)) 'down 010'
    Invoke-Check $Target '009_target_operation_collector_asan_check.sql' 8 '10 copy: 009 check after down'
    if ((Get-Snapshot $Target) -ne $copyBASE) { Stop-E1 'snapshot after down differs from the 009 state' }
    Write-Log '  snapshot after down = 009 state'
    Write-Log '== 11 apply 010 again'
    Assert-Ok (Invoke-Psql $Target @('-1', '-f', $Files['010_object_run.sql'].Path)) 'apply 010 again'
    Invoke-Check $Target '010_object_run_check.sql' 9 '11 copy: 010 check'
    if ((Get-Snapshot $Target) -ne $copyNEW) { Stop-E1 'snapshot after re-apply differs from the 010 state' }
    Write-Log '  snapshot after re-apply = 010 state'
    if (((Get-RowCounts $Target) -join ' ') -ne ($rowsBefore -join ' ')) { Stop-E1 'row counts changed by down/up' }

    # 12. dwh untouched.
    Invoke-Check $Source '009_target_operation_collector_asan_check.sql' 8 '12 dwh: 009 check (read-only)'
    if ((Get-Snapshot $Source) -ne $dwhBefore) { Stop-E1 'dwh catalog snapshot changed' }
    Write-Log '  dwh snapshot unchanged'
    Write-Log "Test database $Target is left with 010 applied (remove only by separate decision)."
    Write-Log 'E1_RESULT: PASS'
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue
    Write-Host "Report: $Report"
}
