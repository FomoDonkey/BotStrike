@echo off
:: Detiene el collector de forma segura creando el archivo STOP_COLLECTOR.
:: El supervisor detecta este archivo y se detiene limpiamente.
echo.
echo  Deteniendo BotStrike Data Collector...
echo.> "%~dp0STOP_COLLECTOR"
echo  Archivo STOP_COLLECTOR creado.
echo  El supervisor se detendra en los proximos 30 segundos.
echo.

:: Tambien detener la tarea programada si esta corriendo
schtasks /End /TN "BotStrike_DataCollector" >nul 2>&1

echo  Listo.
pause
