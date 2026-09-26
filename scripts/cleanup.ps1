<#
Очистка временных файлов проекта (Windows, PowerShell 5.1+).

Удаляет кеши Python и тестов, отчёты покрытия, по флагу -WithLogs чистит
старые файлы логов. Базу данных и .env не трогает.

Использование:
  .\scripts\cleanup.ps1
  .\scripts\cleanup.ps1 -WithLogs
#>

[CmdletBinding()]
param([switch]$WithLogs)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Чищу кеши Python..."
Get-ChildItem -Path . -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
Get-ChildItem -Path . -File -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

foreach ($item in ".pytest_cache", ".ruff_cache", ".mypy_cache", "htmlcov") {
    if (Test-Path $item) { Remove-Item $item -Recurse -Force }
}
Get-ChildItem -Path . -Filter ".coverage*" -File -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

if ($WithLogs) {
    Write-Host "Удаляю старые файлы логов (bot.log остаётся)..."
    Get-ChildItem -Path "src\app\logs" -Filter "bot.log.*" -File -ErrorAction SilentlyContinue |
        Remove-Item -Force
}

$dbPath = Join-Path $root "src\app\database\database.db"
if (Test-Path $dbPath) {
    Write-Host "Сбрасываю WAL и сжимаю базу..."
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
    if ($python) {
        $code = @"
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
conn.execute('VACUUM')
conn.close()
"@
        & $python.Source "-c" $code $dbPath
    } else {
        Write-Warning "python не найден в PATH, пропускаю обслуживание базы."
    }
}

Write-Host "Готово."
