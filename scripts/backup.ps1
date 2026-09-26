<#
Бэкап базы данных бота (Windows, PowerShell 5.1+).

Копия снимается онлайн средствами SQLite, останавливать бота не нужно.

Использование:
  .\scripts\backup.ps1
  .\scripts\backup.ps1 -BackupDir D:\backups -Keep 30
#>

[CmdletBinding()]
param(
    [string]$BackupDir,
    [int]$Keep = 14,
    [string]$DbPath
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
if (-not $DbPath) { $DbPath = Join-Path $root "src\app\database\database.db" }
if (-not $BackupDir) { $BackupDir = Join-Path $root "backups" }

if (-not (Test-Path $DbPath)) {
    Write-Error "Базы нет: $DbPath. Похоже, бот ещё ни разу не запускался."
}

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$target = Join-Path $BackupDir "database-$stamp.db"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) { Write-Error "Не найден python в PATH, он нужен для онлайн-копии базы." }

$code = @"
import sqlite3, sys
source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
with target:
    source.backup(target)
target.close()
source.close()
"@

& $python.Source "-c" $code $DbPath $target
if ($LASTEXITCODE -ne 0) { Write-Error "Не удалось снять копию базы." }

Compress-Archive -Path $target -DestinationPath "$target.zip" -Force
Remove-Item $target
Write-Host "Готово: $target.zip"

if ($Keep -gt 0) {
    Get-ChildItem -Path $BackupDir -Filter "database-*.db.zip" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $Keep |
        ForEach-Object {
            Remove-Item $_.FullName -Force
            Write-Host "Удалил старую копию: $($_.Name)"
        }
}
