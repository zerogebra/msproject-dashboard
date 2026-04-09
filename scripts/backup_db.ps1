$ErrorActionPreference = "Stop"

$sourceDb = "C:\Users\mohammadhamzehCubesP\Downloads\Project Monitoring\data\prism.db"
$backupDir = "D:\DB Backup"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupFile = Join-Path $backupDir ("prism_" + $timestamp + ".db")

if (!(Test-Path $backupDir)) {
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
}

Copy-Item -Path $sourceDb -Destination $backupFile -Force

# Keep latest 30 backups (rolling retention)
$files = Get-ChildItem -Path $backupDir -Filter "prism_*.db" | Sort-Object LastWriteTime -Descending
if ($files.Count -gt 30) {
    $files | Select-Object -Skip 30 | Remove-Item -Force
}

Write-Output ("Backup created: " + $backupFile)
