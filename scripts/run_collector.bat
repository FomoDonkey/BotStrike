@echo off
title BotStrike Data Collector
color 0A
cd /d "C:\Users\edgar\Desktop\proyectos\BotStrike"

:loop
echo [%date% %time%] Starting collector...
"C:\Users\edgar\AppData\Local\Programs\Python\Python312\python.exe" -u "C:\Users\edgar\Desktop\proyectos\BotStrike\main.py" --collect-data
echo.
echo [%date% %time%] Collector stopped. Restarting in 10s...
timeout /t 10 /nobreak
goto loop
