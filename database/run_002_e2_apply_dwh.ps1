# I02-04 M-SS02: E2 - apply 002_secret_store_hardening to the WORKING dwh.
# Approved by the user on 02.11.2026 after E1 PASS on a restored copy.
# Steps: verify dwh is in the 001 state (check 11/11) -> fresh backup
# (pg_dump -Fc + globals without role passwords) -> confirmation by typing
# APPLY -> 002 in ONE transaction (lock_timeout 10s) -> check 15/15 -> row
# counts unchanged. Any failure = STOP; a failed apply rolls back completely.
# Rollback after success: 002_secret_store_hardening.down.sql (written to the
# work folder; works while no binding has a profile_code).
# The dwh_admin password is asked interactively and kept only in this process.
# Run in Windows PowerShell 5.1: powershell -ExecutionPolicy Bypass -File <this file>
$ErrorActionPreference = 'Stop'

$Root   = 'C:\Users\elshad.asgarov\Desktop\DWH\dwh'
$Bin    = 'C:\Program Files\PostgreSQL\17\bin'
$DbHost = '127.0.0.1'
$DbPort = '5433'
$Source = 'dwh'
$Stamp  = Get-Date -Format 'yyyyMMdd_HHmmss'
$Work   = Join-Path $Root "runtime\temp\I02_04_E2_$Stamp"
$Backup = Join-Path $Root 'runtime\backup'
$Report = Join-Path $Work 'E2_report.txt'

$Files = @{
    '001_secret_store_baseline_check.sql' = @{
        Sha  = 'CBC7C24F0FAA73583CAC38607FE1BFDCB964F73D2DBE46B6C819D0BE12BECD19'
        Path = ''
        Data = 'LS0gSTAyLTA0IE0tU1MwMSBjaGVjazogcmVhZC1vbmx5IGNhdGFsb2cgdmVyaWZpY2F0aW9uIG9mIHNlY3JldF9zdG9yZS4KLS0gUGxhaW4gU1FMIChubyBwc3FsIG1ldGEtY29tbWFuZHMpOiBydW5zIHRocm91Z2ggY29ubmVjdF9yZWFkb25seSgpIGFzIGR3aF9hcHAKLS0gb3IgdGhyb3VnaCBwc3FsLiBTRUxFQ1Qgb25seTsgcmVhZHMgcGdfY2F0YWxvZywgbmV2ZXIgcm93IGNvbnRlbnRzLgotLSBFeHBlY3RlZCB3aGVuIHByZXNlbnQ6IGV2ZXJ5IHJlc3VsdCA9IFBBU1MuIFdoZW4gYWJzZW50OiAnc2NoZW1hIGV4aXN0cycgPSBGQUlMCi0tIGFuZCB0aGUgb3RoZXIgY2hlY2tzIEZBSUwgYXMgd2VsbC4KU0VMRUNUIGN1cnJlbnRfc2V0dGluZygndHJhbnNhY3Rpb25fcmVhZF9vbmx5JykgQVMgdHJhbnNhY3Rpb25fcmVhZF9vbmx5LAogICAgICAgY3VycmVudF9kYXRhYmFzZSgpIEFTIGRhdGFiYXNlX25hbWUsCiAgICAgICBjdXJyZW50X3VzZXIgQVMgcm9sZV9uYW1lLAogICAgICAgY3VycmVudF9zZXR0aW5nKCdzZXJ2ZXJfdmVyc2lvbicpIEFTIHNlcnZlcl92ZXJzaW9uOwoKV0lUSCBzIEFTIChTRUxFQ1Qgb2lkLCBuc3Bvd25lciwgbnNwYWNsIEZST00gcGdfbmFtZXNwYWNlIFdIRVJFIG5zcG5hbWUgPSAnc2VjcmV0X3N0b3JlJyksCnQgQVMgKFNFTEVDVCBjLm9pZCwgYy5yZWxuYW1lIEZST00gcGdfY2xhc3MgYyBKT0lOIHMgT04gYy5yZWxuYW1lc3BhY2UgPSBzLm9pZCBXSEVSRSBjLnJlbGtpbmQgPSAncicpLApmIEFTIChTRUxFQ1QgcC5vaWQsIHAucHJvc2VjZGVmIEZST00gcGdfcHJvYyBwIEpPSU4gcyBPTiBwLnByb25hbWVzcGFjZSA9IHMub2lkKSwKY2hlY2tzIEFTICgKICBTRUxFQ1QgMSBBUyBuLCAnc2NoZW1hIGV4aXN0cycgQVMgY2hlY2tfbmFtZSwgKFNFTEVDVCBjb3VudCgqKSBGUk9NIHMpID0gMSBBUyBvawogIFVOSU9OIEFMTCBTRUxFQ1QgMiwgJ3RhYmxlcyBiaW5kaW5nLCBvcGVyYXRpb25fZXZlbnQnLAogICAgY29hbGVzY2UoKFNFTEVDVCBhcnJheV9hZ2cocmVsbmFtZSBPUkRFUiBCWSByZWxuYW1lKSBGUk9NIHQpID0gQVJSQVlbJ2JpbmRpbmcnLCAnb3BlcmF0aW9uX2V2ZW50J106Om5hbWVbXSwgZmFsc2UpCiAgVU5JT04gQUxMIFNFTEVDVCAzLCAnYmluZGluZyBjb25zdHJhaW50cyA9IDYnLAogICAgKFNFTEVDVCBjb3VudCgqKSBGUk9NIHBnX2NvbnN0cmFpbnQgV0hFUkUgY29ucmVsaWQgPSAoU0VMRUNUIG9pZCBGUk9NIHQgV0hFUkUgcmVsbmFtZSA9ICdiaW5kaW5nJykpID0gNgogIFVOSU9OIEFMTCBTRUxFQ1QgNCwgJ29wZXJhdGlvbl9ldmVudCBjb25zdHJhaW50cyA9IDYnLAogICAgKFNFTEVDVCBjb3VudCgqKSBGUk9NIHBnX2NvbnN0cmFpbnQgV0hFUkUgY29ucmVsaWQgPSAoU0VMRUNUIG9pZCBGUk9NIHQgV0hFUkUgcmVsbmFtZSA9ICdvcGVyYXRpb25fZXZlbnQnKSkgPSA2CiAgVU5JT04gQUxMIFNFTEVDVCA1LCAnbmFtZWQgY29uc3RyYWludHMgcHJlc2VudCcsCiAgICAoU0VMRUNUIGNvdW50KCopIEZST00gcGdfY29uc3RyYWludCBXSEVSRSBjb25uYW1lc3BhY2UgPSAoU0VMRUNUIG9pZCBGUk9NIHMpIEFORCBjb25uYW1lIElOICgKICAgICAgJ2NpcGhlcnRleHRfZGlnZXN0X2Zvcm1hdCcsICdiaW5kaW5nX3N0YXRlX3RpbWVzdGFtcHMnLCAnb3BlcmF0aW9uX2NvZGVfYnlfcGhhc2UnLAogICAgICAnb3BlcmF0aW9uX29uY2VfcGVyX3BoYXNlJywgJ2V2ZW50X2Zvcl9jcmVhdGVfb3BlcmF0aW9uJykpID0gNQogIFVOSU9OIEFMTCBTRUxFQ1QgNiwgJ2luZGV4ZXMgcHJlc2VudCcsCiAgICAoU0VMRUNUIGNvdW50KCopIEZST00gcGdfY2xhc3MgV0hFUkUgcmVsbmFtZXNwYWNlID0gKFNFTEVDVCBvaWQgRlJPTSBzKSBBTkQgcmVsa2luZCA9ICdpJwogICAgICAgQU5EIHJlbG5hbWUgSU4gKCdiaW5kaW5nX3NvdXJjZV9zdGF0ZV9pZHgnLCAnb3BlcmF0aW9uX2V2ZW50X3NvdXJjZV9jcmVhdGVkX2lkeCcpKSA9IDIKICBVTklPTiBBTEwgU0VMRUNUIDcsICc0IGd1YXJkIHRyaWdnZXJzIGVuYWJsZWQnLAogICAgKFNFTEVDVCBjb3VudCgqKSBGUk9NIHBnX3RyaWdnZXIgV0hFUkUgdGdyZWxpZCBJTiAoU0VMRUNUIG9pZCBGUk9NIHQpIEFORCBOT1QgdGdpc2ludGVybmFsIEFORCB0Z2VuYWJsZWQgPSAnTycKICAgICAgIEFORCB0Z25hbWUgSU4gKCdiaW5kaW5nX2d1YXJkJywgJ2V2ZW50X2FwcGVuZF9vbmx5JywgJ2V2ZW50X2luc2VydF9ndWFyZCcsICdiaW5kaW5nX2luc2VydF9ndWFyZCcpKSA9IDQKICBVTklPTiBBTEwgU0VMRUNUIDgsICc0IGZ1bmN0aW9ucywgbm9uZSBTRUNVUklUWSBERUZJTkVSJywKICAgIChTRUxFQ1QgY291bnQoKikgRlJPTSBmKSA9IDQgQU5EIE5PVCBFWElTVFMgKFNFTEVDVCAxIEZST00gZiBXSEVSRSBwcm9zZWNkZWYpCiAgVU5JT04gQUxMIFNFTEVDVCA5LCAnc2NoZW1hOiBubyBncmFudHMgYmV5b25kIG93bmVyJywKICAgIChTRUxFQ1QgY291bnQoKikgRlJPTSBzKSA9IDEgQU5EIE5PVCBFWElTVFMgKAogICAgICBTRUxFQ1QgMSBGUk9NIHMsIGFjbGV4cGxvZGUoY29hbGVzY2Uocy5uc3BhY2wsIGFjbGRlZmF1bHQoJ24nLCBzLm5zcG93bmVyKSkpIGEgV0hFUkUgYS5ncmFudGVlIDw+IHMubnNwb3duZXIpCiAgVU5JT04gQUxMIFNFTEVDVCAxMCwgJ3RhYmxlczogbm8gZ3JhbnRzIGJleW9uZCBvd25lcicsCiAgICAoU0VMRUNUIGNvdW50KCopIEZST00gdCkgPSAyIEFORCBOT1QgRVhJU1RTICgKICAgICAgU0VMRUNUIDEgRlJPTSB0IEpPSU4gcGdfY2xhc3MgYyBPTiBjLm9pZCA9IHQub2lkLCBhY2xleHBsb2RlKGNvYWxlc2NlKGMucmVsYWNsLCBhY2xkZWZhdWx0KCdyJywgYy5yZWxvd25lcikpKSBhCiAgICAgICBXSEVSRSBhLmdyYW50ZWUgPD4gYy5yZWxvd25lcikKICBVTklPTiBBTEwgU0VMRUNUIDExLCAnZnVuY3Rpb25zOiBubyBQVUJMSUMgb3Igb3RoZXIgRVhFQ1VURScsCiAgICAoU0VMRUNUIGNvdW50KCopIEZST00gZikgPSA0IEFORCBOT1QgRVhJU1RTICgKICAgICAgU0VMRUNUIDEgRlJPTSBmIEpPSU4gcGdfcHJvYyBwIE9OIHAub2lkID0gZi5vaWQsIGFjbGV4cGxvZGUoY29hbGVzY2UocC5wcm9hY2wsIGFjbGRlZmF1bHQoJ2YnLCBwLnByb293bmVyKSkpIGEKICAgICAgIFdIRVJFIGEuZ3JhbnRlZSA8PiBwLnByb293bmVyKQopClNFTEVDVCBjaGVja19uYW1lLCBDQVNFIFdIRU4gb2sgVEhFTiAnUEFTUycgRUxTRSAnRkFJTCcgRU5EIEFTIHJlc3VsdCBGUk9NIGNoZWNrcyBPUkRFUiBCWSBuOwoKLS0gUm93IGNvdW50cyBvbmx5IChubyBjb250ZW50cykuIE5VTEwgPSB0YWJsZSBhYnNlbnQgb3Igbm8gU0VMRUNUIHByaXZpbGVnZQotLSBmb3IgdGhlIGN1cnJlbnQgcm9sZSAoZXhwZWN0ZWQgZm9yIGR3aF9hcHA6IG5vIGdyYW50cyBieSBkZXNpZ24pLgpTRUxFQ1Qgci5yZWxuYW1lIEFTIHRhYmxlX25hbWUsCiAgICAgICBDQVNFIFdIRU4gYy5vaWQgSVMgTk9UIE5VTEwgVEhFTgogICAgICAgICBDQVNFIFdIRU4gaGFzX3NjaGVtYV9wcml2aWxlZ2UoYy5yZWxuYW1lc3BhY2UsICdVU0FHRScpIEFORCBoYXNfdGFibGVfcHJpdmlsZWdlKGMub2lkLCAnU0VMRUNUJykgVEhFTgogICAgICAgICAgICh4cGF0aCgnL3Jvdy9jL3RleHQoKScsIHF1ZXJ5X3RvX3htbChmb3JtYXQoJ1NFTEVDVCBjb3VudCgqKSBBUyBjIEZST00gc2VjcmV0X3N0b3JlLiVJJywgci5yZWxuYW1lKSwgZmFsc2UsIHRydWUsICcnKSkpWzFdOjp0ZXh0OjpiaWdpbnQKICAgICAgICAgRU5ECiAgICAgICBFTkQgQVMgcm93X2NvdW50CiAgRlJPTSAoVkFMVUVTICgnYmluZGluZycpLCAoJ29wZXJhdGlvbl9ldmVudCcpKSBBUyByKHJlbG5hbWUpCiAgTEVGVCBKT0lOIHBnX2NsYXNzIGMKICAgIE9OIGMucmVsbmFtZSA9IHIucmVsbmFtZSBBTkQgYy5yZWxraW5kID0gJ3InCiAgIEFORCBjLnJlbG5hbWVzcGFjZSA9IChTRUxFQ1Qgb2lkIEZST00gcGdfbmFtZXNwYWNlIFdIRVJFIG5zcG5hbWUgPSAnc2VjcmV0X3N0b3JlJyk7Cg=='
    }
    '002_secret_store_hardening.sql' = @{
        Sha  = '7246E0CDD83DC5DD4C59814A221EC0707A7B5E4A6E65549FC5191EB2F293D668'
        Path = ''
        Data = 'LS0gSTAyLTA0IE0tU1MwMjogc2VjcmV0X3N0b3JlIGhhcmRlbmluZyAocmV2aWV3IGZpbmRpbmdzIEcxLCBHMikuCi0tIEcxOiBUUlVOQ0FURSBubyBsb25nZXIgYnlwYXNzZXMgdGhlIGFwcGVuZC1vbmx5L2ltbXV0YWJsZSByb3cgZ3VhcmRzLgotLSBHMjogYSBzZWNyZXQgaXMgYm91bmQgdG8gYSBjb25uZWN0aW9uIHByb2ZpbGU7IGF0IG1vc3Qgb25lIEFDVElWRSBzZWNyZXQgcGVyCi0tICAgICAoc291cmNlX3N5c3RlbV9pZCwgcHJvZmlsZV9jb2RlKS4gUkVQTEFDRSA9IHJldm9rZSBvbGQsIHRoZW4gYWN0aXZhdGUgbmV3LAotLSAgICAgaW4gb25lIHRyYW5zYWN0aW9uLiBSb3dzIGNyZWF0ZWQgYmVmb3JlIHRoaXMgcGFja2FnZSBrZWVwIHByb2ZpbGVfY29kZQotLSAgICAgTlVMTDogYSBQRU5ESU5HIG9uZSBjYW4gbmV2ZXIgYmVjb21lIEFDVElWRS4gVGhlIHJ1bGUgaXMgTk9UIFZBTElEIHNvCi0tICAgICB0aGF0IGJpbmRpbmdzIGFscmVhZHkgQUNUSVZFIHdpdGhvdXQgYSBwcm9maWxlIChmb3VuZCBpbiBkd2g6IDIgb24KLS0gICAgIENSTV9SRVRBSUwsIDI4LTI5LjEwLjIwMjYpIGFyZSBrZXB0IHVuY2hhbmdlZDsgdGhleSBjYW4gb25seSBiZQotLSAgICAgcmV2b2tlZCwgdGhyb3VnaCBhbiBhdWRpdGVkIG9wZXJhdGlvbiBvbmNlIHRoZSBqb3VybmFsIHN1cHBvcnRzIGl0LgotLSBSZXF1aXJlcyAwMDFfc2VjcmV0X3N0b3JlX2Jhc2VsaW5lLiBSdW4gYXMgdGhlIHNlY3JldF9zdG9yZSBvd25lcjoKLS0gICBwc3FsIC0xIC12IE9OX0VSUk9SX1NUT1A9MSAtZiAwMDJfc2VjcmV0X3N0b3JlX2hhcmRlbmluZy5zcWwKLS0gTm8gYWNjZXNzIGlzIGdyYW50ZWQgdG8gYXBwbGljYXRpb24gb3IgRVRMIHJvbGVzLgoKQ1JFQVRFIEZVTkNUSU9OIHNlY3JldF9zdG9yZS5yZWplY3RfdHJ1bmNhdGUoKSBSRVRVUk5TIHRyaWdnZXIKTEFOR1VBR0UgcGxwZ3NxbCBTRVQgc2VhcmNoX3BhdGggPSBwZ19jYXRhbG9nLCBzZWNyZXRfc3RvcmUgQVMgJGZuJApCRUdJTiBSQUlTRSBFWENFUFRJT04gJ1RSVU5DQVRFIG9mICUgcHJvaGliaXRlZCcsIFRHX1RBQkxFX05BTUU7IEVORDsKJGZuJDsKQ1JFQVRFIFRSSUdHRVIgYmluZGluZ190cnVuY2F0ZV9ndWFyZCBCRUZPUkUgVFJVTkNBVEUgT04gc2VjcmV0X3N0b3JlLmJpbmRpbmcKRk9SIEVBQ0ggU1RBVEVNRU5UIEVYRUNVVEUgRlVOQ1RJT04gc2VjcmV0X3N0b3JlLnJlamVjdF90cnVuY2F0ZSgpOwpDUkVBVEUgVFJJR0dFUiBldmVudF90cnVuY2F0ZV9ndWFyZCBCRUZPUkUgVFJVTkNBVEUgT04gc2VjcmV0X3N0b3JlLm9wZXJhdGlvbl9ldmVudApGT1IgRUFDSCBTVEFURU1FTlQgRVhFQ1VURSBGVU5DVElPTiBzZWNyZXRfc3RvcmUucmVqZWN0X3RydW5jYXRlKCk7CgotLSBQcm9maWxlIGNvZGUgZm9sbG93cyB0aGUgcHJvamVjdCBjb2RlIGNvbnZlbnRpb24gKG1vZHVsZS9yb2xlIGNvZGVzKS4KLS0gVGhlIGZ1dHVyZSBpbnRlZ3JhdGlvbl9wcm9maWxlIChJMDMpIGlzIGV4cGVjdGVkIHRvIHJlZmVyZW5jZSB0aGlzIHBhaXIuCkFMVEVSIFRBQkxFIHNlY3JldF9zdG9yZS5iaW5kaW5nIEFERCBDT0xVTU4gcHJvZmlsZV9jb2RlIHRleHQ7CkFMVEVSIFRBQkxFIHNlY3JldF9zdG9yZS5iaW5kaW5nIEFERCBDT05TVFJBSU5UIGJpbmRpbmdfcHJvZmlsZV9jb2RlX2Zvcm1hdAogIENIRUNLIChwcm9maWxlX2NvZGUgSVMgTlVMTCBPUiBwcm9maWxlX2NvZGUgfiAnXltBLVpdW0EtWjAtOV9dezAsNjN9JCcpOwpBTFRFUiBUQUJMRSBzZWNyZXRfc3RvcmUuYmluZGluZyBBREQgQ09OU1RSQUlOVCBiaW5kaW5nX2FjdGl2ZV9yZXF1aXJlc19wcm9maWxlCiAgQ0hFQ0sgKHN0YXRlIDw+ICdBQ1RJVkUnIE9SIHByb2ZpbGVfY29kZSBJUyBOT1QgTlVMTCkgTk9UIFZBTElEOwpDUkVBVEUgVU5JUVVFIElOREVYIGJpbmRpbmdfb25lX2FjdGl2ZV9wZXJfcHJvZmlsZQogIE9OIHNlY3JldF9zdG9yZS5iaW5kaW5nKHNvdXJjZV9zeXN0ZW1faWQsIHByb2ZpbGVfY29kZSkgV0hFUkUgc3RhdGUgPSAnQUNUSVZFJzsKCkNSRUFURSBGVU5DVElPTiBzZWNyZXRfc3RvcmUucmVqZWN0X3Byb2ZpbGVfY2hhbmdlKCkgUkVUVVJOUyB0cmlnZ2VyCkxBTkdVQUdFIHBscGdzcWwgU0VUIHNlYXJjaF9wYXRoID0gcGdfY2F0YWxvZywgc2VjcmV0X3N0b3JlIEFTICRmbiQKQkVHSU4KICBJRiBORVcucHJvZmlsZV9jb2RlIElTIERJU1RJTkNUIEZST00gT0xELnByb2ZpbGVfY29kZSBUSEVOCiAgICBSQUlTRSBFWENFUFRJT04gJ0JpbmRpbmcgcHJvZmlsZSBpbW11dGFibGUnOwogIEVORCBJRjsKICBSRVRVUk4gTkVXOwpFTkQ7CiRmbiQ7CkNSRUFURSBUUklHR0VSIGJpbmRpbmdfcHJvZmlsZV9ndWFyZCBCRUZPUkUgVVBEQVRFIE9OIHNlY3JldF9zdG9yZS5iaW5kaW5nCkZPUiBFQUNIIFJPVyBFWEVDVVRFIEZVTkNUSU9OIHNlY3JldF9zdG9yZS5yZWplY3RfcHJvZmlsZV9jaGFuZ2UoKTsKClJFVk9LRSBBTEwgT04gRlVOQ1RJT04gc2VjcmV0X3N0b3JlLnJlamVjdF90cnVuY2F0ZSgpIEZST00gUFVCTElDOwpSRVZPS0UgQUxMIE9OIEZVTkNUSU9OIHNlY3JldF9zdG9yZS5yZWplY3RfcHJvZmlsZV9jaGFuZ2UoKSBGUk9NIFBVQkxJQzsK'
    }
    '002_secret_store_hardening.down.sql' = @{
        Sha  = 'EF77F64811BA10863AE1AC0A4052B226C0D098CDD6D90145D2A55F35CF5EDD50'
        Path = ''
        Data = 'LS0gSTAyLTA0IE0tU1MwMiByb2xsYmFjazogcmV0dXJuIHNlY3JldF9zdG9yZSB0byB0aGUgMDAxX3NlY3JldF9zdG9yZV9iYXNlbGluZSBzdGF0ZS4KLS0gUmVmdXNlcyBhbmQgY2hhbmdlcyBub3RoaW5nIGlmIGFueSBiaW5kaW5nIGhhcyBhIHByb2ZpbGVfY29kZSAoZHJvcHBpbmcgdGhlCi0tIGNvbHVtbiB3b3VsZCBsb3NlIGl0KS4gRHJvcHMgd2l0aG91dCBDQVNDQURFOiB1bmV4cGVjdGVkIGRlcGVuZGVudHMgYWJvcnQgaXQuCi0tIFJ1biBhcyB0aGUgc2VjcmV0X3N0b3JlIG93bmVyOiBwc3FsIC12IE9OX0VSUk9SX1NUT1A9MSAtZiAwMDJfc2VjcmV0X3N0b3JlX2hhcmRlbmluZy5kb3duLnNxbApCRUdJTjsKTE9DSyBUQUJMRSBzZWNyZXRfc3RvcmUuYmluZGluZyBJTiBBQ0NFU1MgRVhDTFVTSVZFIE1PREU7CkRPICRkbyQKQkVHSU4KICBJRiBFWElTVFMgKFNFTEVDVCAxIEZST00gc2VjcmV0X3N0b3JlLmJpbmRpbmcgV0hFUkUgcHJvZmlsZV9jb2RlIElTIE5PVCBOVUxMKSBUSEVOCiAgICBSQUlTRSBFWENFUFRJT04gJ1JPTExCQUNLIFJFRlVTRUQ6IGJpbmRpbmdzIHdpdGggcHJvZmlsZV9jb2RlIGV4aXN0JzsKICBFTkQgSUY7CkVORAokZG8kOwpEUk9QIFRSSUdHRVIgYmluZGluZ19wcm9maWxlX2d1YXJkIE9OIHNlY3JldF9zdG9yZS5iaW5kaW5nOwpEUk9QIEZVTkNUSU9OIHNlY3JldF9zdG9yZS5yZWplY3RfcHJvZmlsZV9jaGFuZ2UoKTsKRFJPUCBJTkRFWCBzZWNyZXRfc3RvcmUuYmluZGluZ19vbmVfYWN0aXZlX3Blcl9wcm9maWxlOwpBTFRFUiBUQUJMRSBzZWNyZXRfc3RvcmUuYmluZGluZyBEUk9QIENPTlNUUkFJTlQgYmluZGluZ19hY3RpdmVfcmVxdWlyZXNfcHJvZmlsZTsKQUxURVIgVEFCTEUgc2VjcmV0X3N0b3JlLmJpbmRpbmcgRFJPUCBDT05TVFJBSU5UIGJpbmRpbmdfcHJvZmlsZV9jb2RlX2Zvcm1hdDsKQUxURVIgVEFCTEUgc2VjcmV0X3N0b3JlLmJpbmRpbmcgRFJPUCBDT0xVTU4gcHJvZmlsZV9jb2RlOwpEUk9QIFRSSUdHRVIgZXZlbnRfdHJ1bmNhdGVfZ3VhcmQgT04gc2VjcmV0X3N0b3JlLm9wZXJhdGlvbl9ldmVudDsKRFJPUCBUUklHR0VSIGJpbmRpbmdfdHJ1bmNhdGVfZ3VhcmQgT04gc2VjcmV0X3N0b3JlLmJpbmRpbmc7CkRST1AgRlVOQ1RJT04gc2VjcmV0X3N0b3JlLnJlamVjdF90cnVuY2F0ZSgpOwpDT01NSVQ7Cg=='
    }
    '002_secret_store_hardening_check.sql' = @{
        Sha  = 'C293F4D5BF36F2B5202C90AAB95521506A984841ACE57844D38EECFD49BCE978'
        Path = ''
        Data = 'LS0gSTAyLTA0IE0tU1MwMiBjaGVjazogcmVhZC1vbmx5IGNhdGFsb2cgdmVyaWZpY2F0aW9uIG9mIHNlY3JldF9zdG9yZSBhZnRlcgotLSAwMDEgKyAwMDIuIFN1cGVyc2VkZXMgMDAxX3NlY3JldF9zdG9yZV9iYXNlbGluZV9jaGVjay5zcWwgb25jZSAwMDIgaXMgYXBwbGllZAotLSAoMDAxJ3Mgb2JqZWN0IGNvdW50cyBubyBsb25nZXIgbWF0Y2gpLiBQbGFpbiBTUUw7IFNFTEVDVCBvbmx5OyByZWFkcwotLSBwZ19jYXRhbG9nLCBuZXZlciByb3cgY29udGVudHMuIEV4cGVjdGVkOiAxNSByZXN1bHRzLCBldmVyeSBvbmUgUEFTUy4KU0VMRUNUIGN1cnJlbnRfc2V0dGluZygndHJhbnNhY3Rpb25fcmVhZF9vbmx5JykgQVMgdHJhbnNhY3Rpb25fcmVhZF9vbmx5LAogICAgICAgY3VycmVudF9kYXRhYmFzZSgpIEFTIGRhdGFiYXNlX25hbWUsCiAgICAgICBjdXJyZW50X3VzZXIgQVMgcm9sZV9uYW1lLAogICAgICAgY3VycmVudF9zZXR0aW5nKCdzZXJ2ZXJfdmVyc2lvbicpIEFTIHNlcnZlcl92ZXJzaW9uOwoKV0lUSCBzIEFTIChTRUxFQ1Qgb2lkLCBuc3Bvd25lciwgbnNwYWNsIEZST00gcGdfbmFtZXNwYWNlIFdIRVJFIG5zcG5hbWUgPSAnc2VjcmV0X3N0b3JlJyksCnQgQVMgKFNFTEVDVCBjLm9pZCwgYy5yZWxuYW1lIEZST00gcGdfY2xhc3MgYyBKT0lOIHMgT04gYy5yZWxuYW1lc3BhY2UgPSBzLm9pZCBXSEVSRSBjLnJlbGtpbmQgPSAncicpLApmIEFTIChTRUxFQ1QgcC5vaWQsIHAucHJvbmFtZSwgcC5wcm9zZWNkZWYgRlJPTSBwZ19wcm9jIHAgSk9JTiBzIE9OIHAucHJvbmFtZXNwYWNlID0gcy5vaWQpLApiIEFTIChTRUxFQ1Qgb2lkIEZST00gdCBXSEVSRSByZWxuYW1lID0gJ2JpbmRpbmcnKSwKY2hlY2tzIEFTICgKICBTRUxFQ1QgMSBBUyBuLCAnc2NoZW1hIGV4aXN0cycgQVMgY2hlY2tfbmFtZSwgKFNFTEVDVCBjb3VudCgqKSBGUk9NIHMpID0gMSBBUyBvawogIFVOSU9OIEFMTCBTRUxFQ1QgMiwgJ3RhYmxlcyBiaW5kaW5nLCBvcGVyYXRpb25fZXZlbnQnLAogICAgY29hbGVzY2UoKFNFTEVDVCBhcnJheV9hZ2cocmVsbmFtZSBPUkRFUiBCWSByZWxuYW1lKSBGUk9NIHQpID0gQVJSQVlbJ2JpbmRpbmcnLCAnb3BlcmF0aW9uX2V2ZW50J106Om5hbWVbXSwgZmFsc2UpCiAgVU5JT04gQUxMIFNFTEVDVCAzLCAnYmluZGluZy5wcm9maWxlX2NvZGUgdGV4dCwgbnVsbGFibGUnLAogICAgRVhJU1RTIChTRUxFQ1QgMSBGUk9NIHBnX2F0dHJpYnV0ZSBhLCBiIFdIRVJFIGEuYXR0cmVsaWQgPSBiLm9pZCBBTkQgYS5hdHRuYW1lID0gJ3Byb2ZpbGVfY29kZScKICAgICAgICAgICAgICBBTkQgTk9UIGEuYXR0aXNkcm9wcGVkIEFORCBOT1QgYS5hdHRub3RudWxsIEFORCBhLmF0dHR5cGlkID0gJ3RleHQnOjpyZWd0eXBlKQogIFVOSU9OIEFMTCBTRUxFQ1QgNCwgJ2JpbmRpbmcgY29uc3RyYWludHMgPSA4JywKICAgIChTRUxFQ1QgY291bnQoKikgRlJPTSBwZ19jb25zdHJhaW50IFdIRVJFIGNvbnJlbGlkID0gKFNFTEVDVCBvaWQgRlJPTSBiKSkgPSA4CiAgVU5JT04gQUxMIFNFTEVDVCA1LCAnb3BlcmF0aW9uX2V2ZW50IGNvbnN0cmFpbnRzID0gNicsCiAgICAoU0VMRUNUIGNvdW50KCopIEZST00gcGdfY29uc3RyYWludCBXSEVSRSBjb25yZWxpZCA9IChTRUxFQ1Qgb2lkIEZST00gdCBXSEVSRSByZWxuYW1lID0gJ29wZXJhdGlvbl9ldmVudCcpKSA9IDYKICBVTklPTiBBTEwgU0VMRUNUIDYsICduYW1lZCBjb25zdHJhaW50cyBwcmVzZW50JywKICAgIChTRUxFQ1QgY291bnQoKikgRlJPTSBwZ19jb25zdHJhaW50IFdIRVJFIGNvbm5hbWVzcGFjZSA9IChTRUxFQ1Qgb2lkIEZST00gcykgQU5EIGNvbm5hbWUgSU4gKAogICAgICAnY2lwaGVydGV4dF9kaWdlc3RfZm9ybWF0JywgJ2JpbmRpbmdfc3RhdGVfdGltZXN0YW1wcycsICdvcGVyYXRpb25fY29kZV9ieV9waGFzZScsCiAgICAgICdvcGVyYXRpb25fb25jZV9wZXJfcGhhc2UnLCAnZXZlbnRfZm9yX2NyZWF0ZV9vcGVyYXRpb24nLAogICAgICAnYmluZGluZ19wcm9maWxlX2NvZGVfZm9ybWF0JywgJ2JpbmRpbmdfYWN0aXZlX3JlcXVpcmVzX3Byb2ZpbGUnKSkgPSA3CiAgVU5JT04gQUxMIFNFTEVDVCA3LCAnQUNUSVZFIHJlcXVpcmVzIHByb2ZpbGU6IE5PVCBWQUxJRCAobGVnYWN5IHJvd3Mga2VwdCknLAogICAgRVhJU1RTIChTRUxFQ1QgMSBGUk9NIHBnX2NvbnN0cmFpbnQgV0hFUkUgY29ucmVsaWQgPSAoU0VMRUNUIG9pZCBGUk9NIGIpCiAgICAgICAgICAgICBBTkQgY29ubmFtZSA9ICdiaW5kaW5nX2FjdGl2ZV9yZXF1aXJlc19wcm9maWxlJyBBTkQgY29udHlwZSA9ICdjJyBBTkQgTk9UIGNvbnZhbGlkYXRlZCkKICBVTklPTiBBTEwgU0VMRUNUIDgsICdvbmUgQUNUSVZFIHBlciBwcm9maWxlOiB1bmlxdWUgcGFydGlhbCBpbmRleCcsCiAgICBFWElTVFMgKFNFTEVDVCAxIEZST00gcGdfaW5kZXggaSBKT0lOIHBnX2NsYXNzIGMgT04gYy5vaWQgPSBpLmluZGV4cmVsaWQKICAgICAgICAgICAgIFdIRVJFIGMucmVsbmFtZSA9ICdiaW5kaW5nX29uZV9hY3RpdmVfcGVyX3Byb2ZpbGUnIEFORCBpLmluZHJlbGlkID0gKFNFTEVDVCBvaWQgRlJPTSBiKQogICAgICAgICAgICAgICBBTkQgaS5pbmRpc3VuaXF1ZSBBTkQgaS5pbmRpc3ZhbGlkIEFORCBpLmluZHByZWQgSVMgTk9UIE5VTEwpCiAgVU5JT04gQUxMIFNFTEVDVCA5LCAnYmFzZWxpbmUgaW5kZXhlcyBwcmVzZW50JywKICAgIChTRUxFQ1QgY291bnQoKikgRlJPTSBwZ19jbGFzcyBXSEVSRSByZWxuYW1lc3BhY2UgPSAoU0VMRUNUIG9pZCBGUk9NIHMpIEFORCByZWxraW5kID0gJ2knCiAgICAgICBBTkQgcmVsbmFtZSBJTiAoJ2JpbmRpbmdfc291cmNlX3N0YXRlX2lkeCcsICdvcGVyYXRpb25fZXZlbnRfc291cmNlX2NyZWF0ZWRfaWR4JykpID0gMgogIFVOSU9OIEFMTCBTRUxFQ1QgMTAsICc3IGd1YXJkIHRyaWdnZXJzIGVuYWJsZWQgKGluY2wuIDIgVFJVTkNBVEUpJywKICAgIChTRUxFQ1QgY291bnQoKikgRlJPTSBwZ190cmlnZ2VyIFdIRVJFIHRncmVsaWQgSU4gKFNFTEVDVCBvaWQgRlJPTSB0KSBBTkQgTk9UIHRnaXNpbnRlcm5hbCBBTkQgdGdlbmFibGVkID0gJ08nCiAgICAgICBBTkQgdGduYW1lIElOICgnYmluZGluZ19ndWFyZCcsICdldmVudF9hcHBlbmRfb25seScsICdldmVudF9pbnNlcnRfZ3VhcmQnLCAnYmluZGluZ19pbnNlcnRfZ3VhcmQnLAogICAgICAgICAgICAgICAgICAgICAgJ2JpbmRpbmdfdHJ1bmNhdGVfZ3VhcmQnLCAnZXZlbnRfdHJ1bmNhdGVfZ3VhcmQnLCAnYmluZGluZ19wcm9maWxlX2d1YXJkJykpID0gNwogIFVOSU9OIEFMTCBTRUxFQ1QgMTEsICdUUlVOQ0FURSB0cmlnZ2VycyBmaXJlIGJlZm9yZSBzdGF0ZW1lbnQnLAogICAgKFNFTEVDVCBjb3VudCgqKSBGUk9NIHBnX3RyaWdnZXIgV0hFUkUgdGdyZWxpZCBJTiAoU0VMRUNUIG9pZCBGUk9NIHQpIEFORCBOT1QgdGdpc2ludGVybmFsCiAgICAgICBBTkQgdGduYW1lIElOICgnYmluZGluZ190cnVuY2F0ZV9ndWFyZCcsICdldmVudF90cnVuY2F0ZV9ndWFyZCcpCiAgICAgICBBTkQgKHRndHlwZSAmIDMyKSA8PiAwIEFORCAodGd0eXBlICYgMikgPD4gMCBBTkQgKHRndHlwZSAmIDEpID0gMCkgPSAyCiAgVU5JT04gQUxMIFNFTEVDVCAxMiwgJzYgZnVuY3Rpb25zLCBub25lIFNFQ1VSSVRZIERFRklORVInLAogICAgKFNFTEVDVCBjb3VudCgqKSBGUk9NIGYpID0gNiBBTkQgTk9UIEVYSVNUUyAoU0VMRUNUIDEgRlJPTSBmIFdIRVJFIHByb3NlY2RlZikKICBVTklPTiBBTEwgU0VMRUNUIDEzLCAnc2NoZW1hOiBubyBncmFudHMgYmV5b25kIG93bmVyJywKICAgIChTRUxFQ1QgY291bnQoKikgRlJPTSBzKSA9IDEgQU5EIE5PVCBFWElTVFMgKAogICAgICBTRUxFQ1QgMSBGUk9NIHMsIGFjbGV4cGxvZGUoY29hbGVzY2Uocy5uc3BhY2wsIGFjbGRlZmF1bHQoJ24nLCBzLm5zcG93bmVyKSkpIGEgV0hFUkUgYS5ncmFudGVlIDw+IHMubnNwb3duZXIpCiAgVU5JT04gQUxMIFNFTEVDVCAxNCwgJ3RhYmxlczogbm8gZ3JhbnRzIGJleW9uZCBvd25lcicsCiAgICAoU0VMRUNUIGNvdW50KCopIEZST00gdCkgPSAyIEFORCBOT1QgRVhJU1RTICgKICAgICAgU0VMRUNUIDEgRlJPTSB0IEpPSU4gcGdfY2xhc3MgYyBPTiBjLm9pZCA9IHQub2lkLCBhY2xleHBsb2RlKGNvYWxlc2NlKGMucmVsYWNsLCBhY2xkZWZhdWx0KCdyJywgYy5yZWxvd25lcikpKSBhCiAgICAgICBXSEVSRSBhLmdyYW50ZWUgPD4gYy5yZWxvd25lcikKICBVTklPTiBBTEwgU0VMRUNUIDE1LCAnZnVuY3Rpb25zOiBubyBQVUJMSUMgb3Igb3RoZXIgRVhFQ1VURScsCiAgICAoU0VMRUNUIGNvdW50KCopIEZST00gZikgPSA2IEFORCBOT1QgRVhJU1RTICgKICAgICAgU0VMRUNUIDEgRlJPTSBmIEpPSU4gcGdfcHJvYyBwIE9OIHAub2lkID0gZi5vaWQsIGFjbGV4cGxvZGUoY29hbGVzY2UocC5wcm9hY2wsIGFjbGRlZmF1bHQoJ2YnLCBwLnByb293bmVyKSkpIGEKICAgICAgIFdIRVJFIGEuZ3JhbnRlZSA8PiBwLnByb293bmVyKQopClNFTEVDVCBjaGVja19uYW1lLCBDQVNFIFdIRU4gb2sgVEhFTiAnUEFTUycgRUxTRSAnRkFJTCcgRU5EIEFTIHJlc3VsdCBGUk9NIGNoZWNrcyBPUkRFUiBCWSBuOwoKLS0gUm93IGNvdW50cyBvbmx5IChubyBjb250ZW50cykuIE5VTEwgPSB0YWJsZSBhYnNlbnQgb3Igbm8gU0VMRUNUIHByaXZpbGVnZQotLSBmb3IgdGhlIGN1cnJlbnQgcm9sZSAoZXhwZWN0ZWQgZm9yIGR3aF9hcHA6IG5vIGdyYW50cyBieSBkZXNpZ24pLgpTRUxFQ1Qgci5yZWxuYW1lIEFTIHRhYmxlX25hbWUsCiAgICAgICBDQVNFIFdIRU4gYy5vaWQgSVMgTk9UIE5VTEwgVEhFTgogICAgICAgICBDQVNFIFdIRU4gaGFzX3NjaGVtYV9wcml2aWxlZ2UoYy5yZWxuYW1lc3BhY2UsICdVU0FHRScpIEFORCBoYXNfdGFibGVfcHJpdmlsZWdlKGMub2lkLCAnU0VMRUNUJykgVEhFTgogICAgICAgICAgICh4cGF0aCgnL3Jvdy9jL3RleHQoKScsIHF1ZXJ5X3RvX3htbChmb3JtYXQoJ1NFTEVDVCBjb3VudCgqKSBBUyBjIEZST00gc2VjcmV0X3N0b3JlLiVJJywgci5yZWxuYW1lKSwgZmFsc2UsIHRydWUsICcnKSkpWzFdOjp0ZXh0OjpiaWdpbnQKICAgICAgICAgRU5ECiAgICAgICBFTkQgQVMgcm93X2NvdW50CiAgRlJPTSAoVkFMVUVTICgnYmluZGluZycpLCAoJ29wZXJhdGlvbl9ldmVudCcpKSBBUyByKHJlbG5hbWUpCiAgTEVGVCBKT0lOIHBnX2NsYXNzIGMKICAgIE9OIGMucmVsbmFtZSA9IHIucmVsbmFtZSBBTkQgYy5yZWxraW5kID0gJ3InCiAgIEFORCBjLnJlbG5hbWVzcGFjZSA9IChTRUxFQ1Qgb2lkIEZST00gcGdfbmFtZXNwYWNlIFdIRVJFIG5zcG5hbWUgPSAnc2VjcmV0X3N0b3JlJyk7Cg=='
    }
    'catalog_snapshot.sql' = @{
        Sha  = '9F43AB25B2595E7816028D05380C7F7EDF07D64AAFF8206D36595C64765D614F'
        Path = ''
        Data = 'LS0gQ2F0YWxvZyBmaW5nZXJwcmludCBmb3IgYmVmb3JlL2FmdGVyIGNvbXBhcmlzb246IG9iamVjdCBjb3VudCBhbmQgbWQ1IG92ZXIKLS0gcmVsYXRpb25zLCBmdW5jdGlvbnMsIHRyaWdnZXJzLCBjb25zdHJhaW50cywgbmFtZXNwYWNlcyBhbmQgc2VjcmV0X3N0b3JlCi0tIGNvbHVtbnMuIFJlYWQtb25seTsgbm8gcm93IGNvbnRlbnRzLiBPdXRwdXQ6IDxjb3VudD46PG1kNT4KU0VMRUNUIGNvdW50KCopIHx8ICc6JyB8fCBtZDUoc3RyaW5nX2FnZyh4LCAnfCcgT1JERVIgQlkgeCkpIEZST00gKAogIFNFTEVDVCBuLm5zcG5hbWUgfHwgJy4nIHx8IGMucmVsbmFtZSB8fCAnOicgfHwgYy5yZWxraW5kOjp0ZXh0IEFTIHgKICAgIEZST00gcGdfY2xhc3MgYyBKT0lOIHBnX25hbWVzcGFjZSBuIE9OIG4ub2lkID0gYy5yZWxuYW1lc3BhY2UKICAgV0hFUkUgbi5uc3BuYW1lIE5PVCBJTiAoJ3BnX2NhdGFsb2cnLCAnaW5mb3JtYXRpb25fc2NoZW1hJywgJ3BnX3RvYXN0JykKICBVTklPTiBBTEwgU0VMRUNUICdmOicgfHwgbi5uc3BuYW1lIHx8ICcuJyB8fCBwLnByb25hbWUKICAgIEZST00gcGdfcHJvYyBwIEpPSU4gcGdfbmFtZXNwYWNlIG4gT04gbi5vaWQgPSBwLnByb25hbWVzcGFjZQogICBXSEVSRSBuLm5zcG5hbWUgTk9UIElOICgncGdfY2F0YWxvZycsICdpbmZvcm1hdGlvbl9zY2hlbWEnKQogIFVOSU9OIEFMTCBTRUxFQ1QgJ3Q6JyB8fCB0Z25hbWUgRlJPTSBwZ190cmlnZ2VyIFdIRVJFIE5PVCB0Z2lzaW50ZXJuYWwKICBVTklPTiBBTEwgU0VMRUNUICdjOicgfHwgY29ubmFtZQogICAgRlJPTSBwZ19jb25zdHJhaW50IGMgSk9JTiBwZ19uYW1lc3BhY2UgbiBPTiBuLm9pZCA9IGMuY29ubmFtZXNwYWNlCiAgIFdIRVJFIG4ubnNwbmFtZSBOT1QgSU4gKCdwZ19jYXRhbG9nJywgJ2luZm9ybWF0aW9uX3NjaGVtYScpCiAgVU5JT04gQUxMIFNFTEVDVCAnbjonIHx8IG5zcG5hbWUgRlJPTSBwZ19uYW1lc3BhY2UKICBVTklPTiBBTEwgU0VMRUNUICdhOicgfHwgYy5yZWxuYW1lIHx8ICcuJyB8fCBhLmF0dG5hbWUKICAgIEZST00gcGdfYXR0cmlidXRlIGEgSk9JTiBwZ19jbGFzcyBjIE9OIGMub2lkID0gYS5hdHRyZWxpZCBKT0lOIHBnX25hbWVzcGFjZSBuIE9OIG4ub2lkID0gYy5yZWxuYW1lc3BhY2UKICAgV0hFUkUgbi5uc3BuYW1lID0gJ3NlY3JldF9zdG9yZScgQU5EIGEuYXR0bnVtID4gMCBBTkQgTk9UIGEuYXR0aXNkcm9wcGVkCikgczsK'
    }
    'secret_store_row_counts.sql' = @{
        Sha  = 'BF3199763FC8C9522346B5F6BE5EEC48694536252D352C7C9E3C90078D917A36'
        Path = ''
        Data = 'LS0gUm93IGNvdW50cyBvZiBzZWNyZXRfc3RvcmUgYnkgc3RhdGUgKG5vIGNvbnRlbnRzKS4gTmVlZHMgdGhlIHNjaGVtYSBvd25lci4KLS0gT3V0cHV0IGxpbmVzOiBiaW5kaW5nOjxTVEFURT49PG4+LCBvcGVyYXRpb25fZXZlbnQ9PG4+LCBzb3VyY2Vfc3lzdGVtPTxuPgpTRUxFQ1QgJ2JpbmRpbmc6JyB8fCBzdGF0ZSB8fCAnPScgfHwgY291bnQoKikgRlJPTSBzZWNyZXRfc3RvcmUuYmluZGluZyBHUk9VUCBCWSBzdGF0ZQpVTklPTiBBTEwgU0VMRUNUICdvcGVyYXRpb25fZXZlbnQ9JyB8fCBjb3VudCgqKSBGUk9NIHNlY3JldF9zdG9yZS5vcGVyYXRpb25fZXZlbnQKVU5JT04gQUxMIFNFTEVDVCAnc291cmNlX3N5c3RlbT0nIHx8IGNvdW50KCopIEZST00gbWV0YS5zb3VyY2Vfc3lzdGVtCk9SREVSIEJZIDE7Cg=='
    }
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
Write-Log "I02-04 M-SS02 E2 apply to dwh, started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Log "Work folder: $Work"

try {
    # 0. Files and tools.
    foreach ($name in $Files.Keys) {
        $path = Join-Path $Work $name
        [IO.File]::WriteAllBytes($path, [Convert]::FromBase64String($Files[$name].Data))
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        if ($hash -ne $Files[$name].Sha) { Stop-E2 "SHA256 mismatch for $name" }
        $Files[$name].Path = $path
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

    # 3. dwh must be exactly in the 001 state.
    Invoke-Check $Source '001_secret_store_baseline_check.sql' 11 '3 dwh: 001 check (read-only)'
    $before = Get-Snapshot $Source
    $rowsBefore = Get-RowCounts $Source
    Write-Log "  dwh snapshot (001): $before"
    Write-Log "  rows: $($rowsBefore -join ' ')"

    # 4. Backup.
    Write-Log '== 4 backup'
    $dump = Join-Path $Backup "dwh_I02_04_E2_before_002_$Stamp.dump"
    $globals = Join-Path $Backup "dwh_I02_04_E2_before_002_${Stamp}_globals_no_passwords.sql"
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
    $answer = Read-Host 'Type APPLY to apply 002 to the working dwh, anything else to stop'
    if ($answer -cne 'APPLY') { Stop-E2 'not confirmed; nothing was applied' }
    Write-Log '  confirmed by user: APPLY'

    # 6. Apply in one transaction; do not wait for locks indefinitely.
    Write-Log '== 6 apply 002 to dwh'
    $env:PGOPTIONS = '-c lock_timeout=10s'
    try {
        $r = Invoke-Psql $Source @('-1', '-f', $Files['002_secret_store_hardening.sql'].Path)
    } finally {
        Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue
    }
    if ($r.Code -ne 0) {
        $after = Get-Snapshot $Source
        if ($after -eq $before) { Write-Log '  transaction rolled back: dwh catalog unchanged' }
        Stop-E2 "apply 002 failed (exit code $($r.Code))"
    }

    # 7. Verify.
    Invoke-Check $Source '002_secret_store_hardening_check.sql' 15 '7 dwh: 002 check (read-only)'
    $after = Get-Snapshot $Source
    $rowsAfter = Get-RowCounts $Source
    Write-Log "  dwh snapshot (002): $after"
    Write-Log "  rows: $($rowsAfter -join ' ')"
    if ($after -eq $before) { Stop-E2 'catalog snapshot did not change after apply' }
    if (($rowsAfter -join ' ') -ne ($rowsBefore -join ' ')) { Stop-E2 'row counts changed by the migration' }
    Write-Log "Rollback file (only if needed, by decision): $($Files['002_secret_store_hardening.down.sql'].Path)"
    Write-Log 'E2_RESULT: PASS'
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:PGOPTIONS -ErrorAction SilentlyContinue
    Write-Host "Report: $Report"
}
