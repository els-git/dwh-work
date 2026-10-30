param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "check",
        "check-crm",
        "check-dwh",
        "capture-source-metadata",
        "plan-snapshot",
        "extract-snapshot",
        "load-snapshot",
        "validate-stg",
        "transform-ods",
        "refresh-marts",
        "run-dq",
        "audit-mappings",
        "load-mapping-catalog"
    )]
    [string]$Task
)

$ErrorActionPreference = "Stop"

$project = "C:\Users\elshad.asgarov\Desktop\DWH\dwh"
$python = "C:\Python312\python.exe"
$etlSrc = Join-Path $project "etl\src"
$etlPackages = Join-Path $project "etl\packages"

$env:PYTHONPATH = "$etlSrc;$etlPackages"

Set-Location $project

function Run-EtlScript {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptName
    )

    $script = Join-Path $etlSrc $ScriptName

    if (-not (Test-Path $script)) {
        Write-Host "STOP: ETL script not found:"
        Write-Host $script
        exit 1
    }

    Write-Host ""
    Write-Host "=== RUNNING $ScriptName ==="

    & $python -c "import runpy; runpy.run_path(r'$script', run_name='__main__')"

    if ($LASTEXITCODE -ne 0) {
        Write-Host "STOP: $ScriptName failed"
        exit $LASTEXITCODE
    }

    Write-Host "PASSED: $ScriptName"
}

switch ($Task) {
    "check" {
        Write-Host "=== ETL CHECK-ONLY MODE ==="

        Run-EtlScript "check_crm.py"
        Run-EtlScript "check_dwh.py"

        Write-Host ""
        Write-Host "ETL CHECK-ONLY PASSED"
    }

    "check-crm" {
        Run-EtlScript "check_crm.py"
    }

    "check-dwh" {
        Run-EtlScript "check_dwh.py"
    }

    "capture-source-metadata" {
        Run-EtlScript "capture_source_metadata.py"
    }

    "plan-snapshot" {
        Run-EtlScript "plan_snapshot.py"
    }

    "extract-snapshot" {
        Run-EtlScript "extract_snapshot.py"
    }

    "load-snapshot" {
        Run-EtlScript "load_snapshot.py"
    }

    "validate-stg" {
        Run-EtlScript "validate_stg.py"
    }

    "transform-ods" {
        Run-EtlScript "transform_ods.py"
    }

    "refresh-marts" {
        Run-EtlScript "refresh_marts.py"
    }

    "run-dq" {
        Run-EtlScript "run_dq.py"
    }

    "audit-mappings" {
        Run-EtlScript "audit_mappings.py"
    }

    "load-mapping-catalog" {
        Run-EtlScript "load_mapping_catalog.py"
    }
}