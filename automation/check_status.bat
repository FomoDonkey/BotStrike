@echo off
:: Muestra el estado actual del collector.
echo.
echo  ============================================
echo   BotStrike Data Collector - Estado
echo  ============================================
echo.

:: Verificar tarea programada
echo  [Tarea Programada]
schtasks /Query /TN "BotStrike_DataCollector" /FO LIST 2>nul
if %errorlevel% neq 0 (
    echo    No instalada. Ejecuta install_task.ps1 como Admin.
)
echo.

:: Verificar PID
set PID_FILE=%~dp0logs\collector.pid
if exist "%PID_FILE%" (
    set /p PID=<"%PID_FILE%"
    echo  [Proceso Collector]
    echo    PID: %PID%
    tasklist /FI "PID eq %PID%" /NH 2>nul | find /i "python" >nul
    if %errorlevel% equ 0 (
        echo    Estado: CORRIENDO
    ) else (
        echo    Estado: NO CORRIENDO (PID obsoleto)
    )
) else (
    echo  [Proceso Collector]
    echo    Estado: NO CORRIENDO
)
echo.

:: Ultimas lineas del log
set LOG_FILE=%~dp0logs\supervisor.log
if exist "%LOG_FILE%" (
    echo  [Ultimas 10 lineas del log]
    powershell -Command "Get-Content '%LOG_FILE%' -Tail 10"
) else (
    echo  [Log]
    echo    Sin log todavia.
)
echo.

:: Datos recolectados
echo  [Datos Recolectados]
set DATA_DIR=%~dp0..\data\trades
if exist "%DATA_DIR%" (
    for /f %%A in ('dir /b /s "%DATA_DIR%\*.parquet" 2^>nul ^| find /c ".parquet"') do (
        echo    Archivos Parquet de trades: %%A
    )
) else (
    echo    Sin datos todavia.
)
echo.
pause
