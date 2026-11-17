param(
    [Parameter(Mandatory = $true)]
    # I18: the tasks of the old CRM contour (check-crm, snapshots, validate/transform/refresh/DQ) were removed.
    [ValidateSet(
        "check",
        "check-dwh",
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

        Run-EtlScript "check_dwh.py"

        Write-Host ""
        Write-Host "ETL CHECK-ONLY PASSED"
    }

    "check-dwh" {
        Run-EtlScript "check_dwh.py"
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