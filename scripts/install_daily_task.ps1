# Crée (ou remplace) la tâche planifiée Windows qui lance la récupération quotidienne.
#   .\scripts\install_daily_task.ps1                 -> tous les jours à 08:00
#   .\scripts\install_daily_task.ps1 -At "07:30"     -> autre heure
#   .\scripts\install_daily_task.ps1 -Remove         -> supprime la tâche
param(
    [string]$At = "08:00",
    [switch]$Remove
)
$ErrorActionPreference = "Stop"
$taskName = "AssistantCandidatures-Daily"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Tâche '$taskName' supprimée."
    return
}

$script = Join-Path $PSScriptRoot "run_daily.ps1"
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# StartWhenAvailable : si le PC était éteint à l'heure prévue, la tâche part au prochain démarrage.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Récupère les annonces et prépare les dossiers de candidature." `
    -Force | Out-Null

Write-Host "Tâche '$taskName' créée : tous les jours à $At."
Write-Host "Test immédiat : Start-ScheduledTask -TaskName $taskName"
Write-Host "Journaux      : data\logs\daily-AAAA-MM-JJ.log"
