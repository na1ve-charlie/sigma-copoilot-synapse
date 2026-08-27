# Run this script in an Administrator PowerShell window.
$ErrorActionPreference = "Stop"
$taskName = "SigmaMaia"
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "SigmaMaia startup task removed. Program files and logs were kept."
