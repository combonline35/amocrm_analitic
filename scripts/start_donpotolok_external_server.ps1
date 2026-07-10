param(
  [string]$User = "default",
  [string]$Account = "donpotolok",
  [int]$Port = 8018,
  [string]$Model = "openai/gpt-4o-mini",
  [switch]$EnableExternal
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = "C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe"
if (!(Test-Path $Python)) {
  $Python = "python"
}

Set-Location $Root

if ($EnableExternal) {
  & "$PSScriptRoot\enable_external_analysis.ps1" -User $User -Account $Account -Mode anonymized_openrouter -Model $Model
}

$env:PYTHONPATH = "src"
$env:AMO_USER_KEY = $User
$env:AMO_ACCOUNT_KEY = $Account
$env:OPENROUTER_ANALYSIS_MODEL = $Model

Write-Host "Starting amoCRM service on http://127.0.0.1:$Port/conversations?user=$User&account=$Account"
Write-Host "External analysis mode: $(if ($EnableExternal) { 'anonymized_openrouter' } else { 'current account setting' })"
& $Python -m amocrm_service.server --host 127.0.0.1 --port $Port --skip-cleanup
