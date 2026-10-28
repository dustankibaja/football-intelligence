# Register Football Intelligence Live Shadow Task in Windows Task Scheduler
param(
    [string]$TaskName = "FootballIntelligence_LiveShadow"
)

$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
$CmdPath = Join-Path $RepoRoot "scripts\scheduler\football_live_shadow.cmd"

Write-Host "Registering Windows Scheduled Task: $TaskName"
Write-Host "Working Directory: $RepoRoot"
Write-Host "Command Target: $CmdPath"

# Register task using schtasks
$SchCmd = "schtasks /Create /TN `"$TaskName`" /TR `"`"$CmdPath`"`" /SC HOURLY /MO 6 /F"
Invoke-Expression $SchCmd

Write-Host "Querying registered task status:"
schtasks /Query /TN "$TaskName" /FO LIST /V
