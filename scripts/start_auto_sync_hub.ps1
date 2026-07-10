param(
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8022
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = "C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe"

Push-Location $Root
try {
  $env:PYTHONPATH = "src"
  & $Python -m amocrm_service.server --host $HostName --port $Port
} finally {
  Pop-Location
}
