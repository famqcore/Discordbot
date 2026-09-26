<#
Обновление бота из GitHub (Windows, PowerShell 5.1+).

Порядок: бэкап базы -> git pull -> установка зависимостей -> подсказка про
перезапуск. При незакоммиченных изменениях скрипт останавливается.

Использование:
  .\scripts\update.ps1
  .\scripts\update.ps1 -Branch main
#>

[CmdletBinding()]
param([string]$Branch)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "git не найден в PATH."
}

if (git status --porcelain) {
    Write-Error "В рабочей копии есть незакоммиченные изменения. Сохраните их и запустите скрипт снова."
}

if (-not $Branch) { $Branch = (git rev-parse --abbrev-ref HEAD).Trim() }

Write-Host "Делаю бэкап базы перед обновлением..."
try { & (Join-Path $PSScriptRoot "backup.ps1") } catch { Write-Warning "Бэкап пропущен: $_" }

Write-Host "Забираю изменения из origin/$Branch..."
$before = (git rev-parse HEAD).Trim()
git fetch origin $Branch
git checkout $Branch
git pull --ff-only origin $Branch
$after = (git rev-parse HEAD).Trim()

if ($before -eq $after) {
    Write-Host "Обновлений нет, версия уже последняя."
} else {
    Write-Host "Обновлено: $before -> $after"
    git --no-pager log --oneline "$before..$after"
}

$python = $null
if (Test-Path (Join-Path $root ".venv\Scripts\python.exe")) {
    $python = Join-Path $root ".venv\Scripts\python.exe"
} else {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command python3 -ErrorAction SilentlyContinue }
    if ($cmd) { $python = $cmd.Source }
}
if (-not $python) { Write-Error "Не найден python в PATH." }

Write-Host "Ставлю зависимости..."
& $python -m pip install --disable-pip-version-check --require-hashes -r "src\app\requirements.txt"

Write-Host ""
Write-Host "Готово. Перезапустите бота, например:"
Write-Host "  docker compose up -d --build"
