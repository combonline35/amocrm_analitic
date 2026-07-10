param(
  [string]$User = "default",
  [string]$Account = "donpotolok",
  [ValidateSet("local", "anonymized_openrouter")]
  [string]$Mode = "anonymized_openrouter",
  [string]$Model = "openai/gpt-4o-mini"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SettingsPath = Join-Path $Root "data\users\$User\accounts\$Account\account_settings.json"

if (!(Test-Path $SettingsPath)) {
  throw "Account settings not found: $SettingsPath"
}

$settings = Get-Content -Raw -Encoding UTF8 $SettingsPath | ConvertFrom-Json
if ($null -eq $settings.conversation_intelligence) {
  $settings | Add-Member -NotePropertyName conversation_intelligence -NotePropertyValue ([pscustomobject]@{})
}

$settings.conversation_intelligence | Add-Member -Force -NotePropertyName external_analysis -NotePropertyValue ([pscustomobject]@{
  mode = $Mode
  provider = "openrouter"
  model = $Model
})

$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($SettingsPath, ($settings | ConvertTo-Json -Depth 80), $utf8NoBom)
Write-Host "Analysis mode for $User/$Account set to: $Mode ($Model)"
