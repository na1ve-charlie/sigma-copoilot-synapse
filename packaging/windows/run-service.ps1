$ErrorActionPreference = "Stop"
$logDirectory = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$logPath = Join-Path $logDirectory "maia.log"
if ((Test-Path $logPath) -and (Get-Item $logPath).Length -gt 10MB) {
    Move-Item $logPath "$logPath.1" -Force
}
& "$PSScriptRoot\SigmaMaia.exe" --config "$PSScriptRoot\config\app.env" *>> $logPath
exit $LASTEXITCODE
