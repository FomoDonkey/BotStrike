#Requires -Version 5.1
<#
.SYNOPSIS
    BotStrike Data Collector Supervisor para Windows 11.
    Ejecuta python main.py --collect-data con reinicio automatico ante crashes.
    Corre en segundo plano, sin ventana visible.

.DESCRIPTION
    - Detecta la ruta de Python automaticamente
    - Reinicia el collector si el proceso muere (crash, error, etc.)
    - Espera 30 segundos entre reinicios para no saturar
    - Registra cada arranque/reinicio/error en automation/logs/supervisor.log
    - Se detiene limpiamente si encuentra el archivo automation/STOP_COLLECTOR

.NOTES
    NO modifica ningun archivo del proyecto.
    Solo lee main.py y escribe en automation/logs/.
#>

# ── Configuracion ──────────────────────────────────────────────────

$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogDir     = Join-Path $ProjectDir "automation\logs"
$LogFile    = Join-Path $LogDir "supervisor.log"
$StopFile   = Join-Path $ProjectDir "automation\STOP_COLLECTOR"
$PidFile    = Join-Path $LogDir "collector.pid"
$MainScript = Join-Path $ProjectDir "main.py"

# Reintentos
$RestartDelaySec = 30
$MaxConsecutiveFails = 10

# ── Funciones ──────────────────────────────────────────────────────

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] $Message"

    if (-not (Test-Path $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    }

    Add-Content -Path $LogFile -Value $line -Encoding UTF8

    # Rotar log si supera 5MB
    if ((Test-Path $LogFile) -and ((Get-Item $LogFile).Length -gt 5MB)) {
        $backupLog = Join-Path $LogDir "supervisor_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
        Move-Item $LogFile $backupLog -Force
        Write-Log "Log rotado a $backupLog"
    }
}

function Find-Python {
    # Buscar python en PATH
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        return $pythonCmd.Source
    }

    # Buscar en ubicaciones comunes de Windows
    $commonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\python.exe"
    )

    foreach ($p in $commonPaths) {
        if (Test-Path $p) {
            return $p
        }
    }

    return $null
}

function Stop-ExistingCollector {
    if (Test-Path $PidFile) {
        $oldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
        if ($oldPid) {
            $proc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Log "Deteniendo collector anterior (PID $oldPid)"
                Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
            }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
}

# ── Main Loop ──────────────────────────────────────────────────────

Write-Log "=========================================="
Write-Log "BotStrike Collector Supervisor iniciando"
Write-Log "Proyecto: $ProjectDir"
Write-Log "=========================================="

# Verificar que main.py existe
if (-not (Test-Path $MainScript)) {
    Write-Log "ERROR: No se encuentra $MainScript"
    exit 1
}

# Encontrar Python
$PythonExe = Find-Python
if (-not $PythonExe) {
    Write-Log "ERROR: Python no encontrado en el sistema"
    exit 1
}
Write-Log "Python: $PythonExe"

# Limpiar archivo de stop si existe de una sesion anterior
if (Test-Path $StopFile) {
    Remove-Item $StopFile -Force
    Write-Log "Archivo STOP_COLLECTOR eliminado de sesion anterior"
}

# Detener collector previo si quedo corriendo
Stop-ExistingCollector

$consecutiveFails = 0
$runCount = 0

while ($true) {
    # Verificar senal de stop
    if (Test-Path $StopFile) {
        Write-Log "Archivo STOP_COLLECTOR detectado. Deteniendo supervisor."
        Stop-ExistingCollector
        break
    }

    $runCount++
    Write-Log "Iniciando collector (ejecucion #$runCount)"

    try {
        # Ejecutar collector
        $processInfo = New-Object System.Diagnostics.ProcessStartInfo
        $processInfo.FileName = $PythonExe
        $processInfo.Arguments = "`"$MainScript`" --collect-data"
        $processInfo.WorkingDirectory = $ProjectDir
        $processInfo.UseShellExecute = $false
        $processInfo.RedirectStandardOutput = $true
        $processInfo.RedirectStandardError = $true
        $processInfo.CreateNoWindow = $true

        # Variables de entorno para encoding
        $processInfo.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
        $processInfo.EnvironmentVariables["PYTHONUNBUFFERED"] = "1"

        $process = [System.Diagnostics.Process]::Start($processInfo)

        # Guardar PID
        $process.Id | Out-File -FilePath $PidFile -Encoding ASCII -Force
        Write-Log "Collector iniciado (PID $($process.Id))"

        # Esperar a que el proceso termine
        $process.WaitForExit()
        $exitCode = $process.ExitCode

        # Leer stderr para diagnostico
        $stderr = $process.StandardError.ReadToEnd()

        if ($exitCode -eq 0) {
            Write-Log "Collector termino normalmente (exit code 0)"
            $consecutiveFails = 0
        } else {
            $consecutiveFails++
            Write-Log "Collector termino con error (exit code $exitCode, fallo consecutivo #$consecutiveFails)"
            if ($stderr) {
                # Solo las ultimas 5 lineas de error
                $errorLines = ($stderr -split "`n" | Select-Object -Last 5) -join " | "
                Write-Log "Error: $errorLines"
            }
        }

        $process.Dispose()

    } catch {
        $consecutiveFails++
        Write-Log "EXCEPCION al ejecutar collector: $($_.Exception.Message) (fallo #$consecutiveFails)"
    }

    # Limpiar PID
    if (Test-Path $PidFile) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }

    # Verificar senal de stop antes de reiniciar
    if (Test-Path $StopFile) {
        Write-Log "STOP_COLLECTOR detectado tras cierre. No reiniciando."
        break
    }

    # Verificar demasiados fallos consecutivos
    if ($consecutiveFails -ge $MaxConsecutiveFails) {
        Write-Log "ALERTA: $MaxConsecutiveFails fallos consecutivos. Esperando 5 minutos antes de reintentar."
        Start-Sleep -Seconds 300
        $consecutiveFails = 0
    }

    Write-Log "Reiniciando collector en $RestartDelaySec segundos..."
    Start-Sleep -Seconds $RestartDelaySec
}

Write-Log "Supervisor detenido"
