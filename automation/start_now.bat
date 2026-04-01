@echo off
:: Inicia el collector inmediatamente (sin esperar login).
:: Abre una ventana de PowerShell que se puede cerrar manualmente.
echo.
echo  BotStrike Data Collector - Inicio manual
echo  =========================================
echo  Para detener: crear archivo automation\STOP_COLLECTOR
echo  O cerrar esta ventana.
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0collector_supervisor.ps1"
pause
