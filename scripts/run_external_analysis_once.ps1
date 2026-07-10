param(
  [string]$User = "default",
  [string]$Account = "donpotolok",
  [int]$Limit = 10,
  [string]$Model = "openai/gpt-4o-mini",
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = "C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe"
if (!(Test-Path $Python)) {
  $Python = "python"
}

Set-Location $Root
& "$PSScriptRoot\enable_external_analysis.ps1" -User $User -Account $Account -Mode anonymized_openrouter -Model $Model

$env:PYTHONPATH = "src"
$argsList = @(
  "-m", "amocrm_service.cli",
  "--user", $User,
  "--account", $Account,
  "conversations", "analyze",
  "--limit", "$Limit"
)
if ($Force) {
  $argsList += "--force"
}

Write-Host "Running anonymized external analysis for $User/$Account..."
& $Python @argsList
