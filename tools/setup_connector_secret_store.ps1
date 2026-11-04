# I03 (withdrawn 04.11.2026 by I02-05): this script wrote a plain-text master
# key into .env. Master keys are now Windows DPAPI files with a backup:
#   tools\secret_key.ps1 -Action New -KeyId <id> -BackupDir <folder>
#   tools\secret_key.ps1 -Action Activate -KeyId <id>
# See docs\DWH_SECRETSTORE_KEY_RUNBOOK.md.
Write-Host "SETUP_RESULT: STOP (withdrawn; use tools\secret_key.ps1, see docs\DWH_SECRETSTORE_KEY_RUNBOOK.md)"
exit 1
