#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Instala la tarea programada de BotStrike en Task Scheduler de Windows 11.
    DEBE ejecutarse como Administrador.

.DESCRIPTION
    Crea una tarea "BotStrike_DataCollector" que:
    - Se ejecuta al iniciar sesion del usuario actual
    - Corre el supervisor en segundo plano (oculto, sin ventana)
    - Se reinicia automaticamente si falla
    - Persiste tras reinicios del PC

.USAGE
    Click derecho en este archivo > "Ejecutar con PowerShell como Administrador"
    O desde terminal elevada:
    powershell -ExecutionPolicy Bypass -File "install_task.ps1"
#>

$TaskName = "BotStrike_DataCollector"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SupervisorScript = Join-Path $ProjectDir "automation\collector_supervisor.ps1"
$UserName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  BotStrike - Instalador de Tarea Programada" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Proyecto:   $ProjectDir"
Write-Host "  Supervisor: $SupervisorScript"
Write-Host "  Usuario:    $UserName"
Write-Host "  Tarea:      $TaskName"
Write-Host ""

# Verificar que el supervisor existe
if (-not (Test-Path $SupervisorScript)) {
    Write-Host "ERROR: No se encuentra $SupervisorScript" -ForegroundColor Red
    Read-Host "Presiona Enter para salir"
    exit 1
}

# Eliminar tarea existente si hay
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "  Eliminando tarea anterior..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Configurar la accion: ejecutar PowerShell oculto con el supervisor
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$SupervisorScript`"" `
    -WorkingDirectory $ProjectDir

# Trigger: al iniciar sesion del usuario actual
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $UserName

# Settings de la tarea
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -MultipleInstances IgnoreNew `
    -Priority 7

# Registrar la tarea
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggerLogon `
    -Settings $settings `
    -Description "BotStrike Data Collector - Recoleccion continua de datos de Strike Finance" `
    -RunLevel Highest `
    -Force

# Verificar
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host ""
    Write-Host "  INSTALADO CORRECTAMENTE" -ForegroundColor Green
    Write-Host ""
    Write-Host "  La tarea '$TaskName' se ejecutara automaticamente" -ForegroundColor Green
    Write-Host "  cada vez que inicies sesion en Windows." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Para iniciar ahora:" -ForegroundColor Yellow
    Write-Host "    Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor White
    Write-Host ""
    Write-Host "  Para detener:" -ForegroundColor Yellow
    Write-Host "    Crear archivo: automation\STOP_COLLECTOR" -ForegroundColor White
    Write-Host "    O: Stop-ScheduledTask -TaskName '$TaskName'" -ForegroundColor White
    Write-Host ""
    Write-Host "  Para desinstalar:" -ForegroundColor Yellow
    Write-Host "    Ejecutar: automation\uninstall_task.ps1" -ForegroundColor White
    Write-Host ""
} else {
    Write-Host "  ERROR: La tarea no se pudo crear" -ForegroundColor Red
}

Read-Host "Presiona Enter para salir"
