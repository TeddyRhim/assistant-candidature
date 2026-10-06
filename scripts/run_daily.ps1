# Lance la récupération quotidienne des annonces et la préparation des dossiers.
# Appelé par la tâche planifiée Windows ; peut aussi être lancé à la main.
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Environnement introuvable : lance d'abord 'uv sync' dans $projectRoot."
}

$logDirectory = Join-Path $projectRoot "data\logs"
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$logFile = Join-Path $logDirectory ("daily-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

$env:PYTHONIOENCODING = "utf-8"
"=== {0} ===" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss") | Out-File -FilePath $logFile -Append -Encoding utf8
& $python -m src.cli daily *>&1 | Out-File -FilePath $logFile -Append -Encoding utf8
$exitCode = $LASTEXITCODE

# Garde les 30 derniers journaux.
Get-ChildItem $logDirectory -Filter "daily-*.log" |
    Sort-Object Name -Descending |
    Select-Object -Skip 30 |
    Remove-Item -Force

exit $exitCode
