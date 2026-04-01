#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Desinstala la tarea programada de BotStrike y detiene el collector.
#>

$TaskName = "BotStrike_DataCollector"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PidFile = Join-Path $ProjectDir "automation\logs\collector.pid"

Write-Host ""
Write-Host "Desinstalando BotStrike Data Collector..." -ForegroundColor Yellow

# Detener proceso si esta corriendo
if (Test-Path $PidFile) {
    $pid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($pid) {
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  Deteniendo collector (PID $pid)..." -ForegroundColor Yellow
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

# Detener la tarea
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

# Eliminar la tarea
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "  Tarea '$TaskName' eliminada" -ForegroundColor Green
} else {
    Write-Host "  Tarea '$TaskName' no encontrada (ya estaba eliminada)" -ForegroundColor Gray
}

# Limpiar archivo STOP si existe
$stopFile = Join-Path $ProjectDir "automation\STOP_COLLECTOR"
if (Test-Path $stopFile) {
    Remove-Item $stopFile -Force
}

Write-Host ""
Write-Host "  Desinstalacion completa." -ForegroundColor Green
Write-Host "  Los datos recolectados en data/ NO se han borrado." -ForegroundColor Cyan
Write-Host ""
Read-Host "Presiona Enter para salir"
