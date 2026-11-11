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
        "load-mapping-catalog",
        "run-object"
    )]
    [string]$Task,

    # run-object: id of the published source object (meta.source_object.source_object_id).
    [long]$ObjectId = 0,

    # run-object: ALL (STG load, then the ODS step if the object has a published ODS version)
    # or ODS (the ODS step alone, from the STG table as it is).
    [ValidateSet("ALL", "ODS")]
    [string]$Stage = "ALL"
)

$ErrorActionPreference = "Stop"

$project = "C:\Users\elshad.asgarov\Desktop\DWH\dwh"
$python = "C:\Python312\python.exe"
$etlSrc = Join-Path $project "etl\src"
$etlPackages = Join-Path $project "etl\packages"

# backend/src and backend/packages: SecretStore modules for the CRM credential (I04-05).
$backendSrc = Join-Path $project "backend\src"
$backendPackages = Join-Path $project "backend\packages"
$env:PYTHONPATH = "$etlSrc;$etlPackages;$backendSrc;$backendPackages"

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

    "run-object" {
        # I06: load one published object by its configuration (plan I06, R3).
        if ($ObjectId -le 0) {
            Write-Host "STOP: -ObjectId <source object id> is required for run-object"
            exit 2
        }
        $env:DWH_RUN_OBJECT_ID = [string]$ObjectId
        $env:DWH_RUN_OBJECT_STAGE = $Stage
        try {
            Run-EtlScript "run_object.py"
        } finally {
            Remove-Item Env:DWH_RUN_OBJECT_ID -ErrorAction SilentlyContinue
            Remove-Item Env:DWH_RUN_OBJECT_STAGE -ErrorAction SilentlyContinue
        }
    }
}