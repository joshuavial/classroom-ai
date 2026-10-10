# Registers a scheduled task that starts WSL when Windows starts and keeps it
# running, so Docker inside Ubuntu brings the stack back with nobody signed in.
# Not yet run on a Windows machine. Check on the Windows test machine.
#
# In an administrator PowerShell:
#   .\scripts\windows\start-at-boot.ps1 -User classroomai -Distro Ubuntu-24.04
param(
    [Parameter(Mandatory = $true)][string]$User,
    [string]$Distro = "Ubuntu-24.04"
)
$ErrorActionPreference = "Stop"

$credential = Get-Credential -UserName $User -Message "Password for $User; the task runs as this account"
# sleep infinity keeps the WSL instance, and with it Docker, running.
$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro --exec /usr/bin/sleep infinity"
$trigger = New-ScheduledTaskTrigger -AtStartup
# A zero time limit means the task is never stopped for running too long.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
# Giving the password makes it "run whether the user is signed in or not".
Register-ScheduledTask -TaskName "classroom-ai WSL" -Action $action -Trigger $trigger `
    -Settings $settings -User $User -Password $credential.GetNetworkCredential().Password `
    -RunLevel Limited -Force | Out-Null
Write-Host "Registered the 'classroom-ai WSL' task. Restart Windows without signing in to check it."
