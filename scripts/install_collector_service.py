"""
Instala el Data Collector de BotStrike para inicio automatico en Windows.

Crea un acceso directo en la carpeta Startup del usuario, de modo que el
collector arranca automaticamente al iniciar sesion. NO requiere admin.

Uso:
    python scripts/install_collector_service.py install   # Instala autostart
    python scripts/install_collector_service.py uninstall # Elimina autostart
    python scripts/install_collector_service.py start     # Inicia ahora
    python scripts/install_collector_service.py stop      # Detiene
    python scripts/install_collector_service.py status    # Muestra estado
"""
import os
import sys
import subprocess

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_EXE = sys.executable
MAIN_PY = os.path.join(PROJECT_DIR, "main.py")
LOG_FILE = os.path.join(PROJECT_DIR, "logs", "collector_service.log")
WRAPPER_BAT = os.path.join(PROJECT_DIR, "scripts", "run_collector.bat")

# Carpeta Startup del usuario (no requiere admin)
STARTUP_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
)
SHORTCUT_NAME = "BotStrike_Collector.bat"
SHORTCUT_PATH = os.path.join(STARTUP_DIR, SHORTCUT_NAME)


def _create_wrapper():
    """Crea el .bat que ejecuta el collector con output visible en pantalla."""
    os.makedirs(os.path.join(PROJECT_DIR, "logs"), exist_ok=True)
    os.makedirs(os.path.dirname(WRAPPER_BAT), exist_ok=True)
    # python -u = unbuffered, output va directo a la ventana del .bat
    # El collector tambien guarda log via structlog (archivo)
    with open(WRAPPER_BAT, "w") as f:
        f.write(f'@echo off\n')
        f.write(f'title BotStrike Data Collector\n')
        f.write(f'color 0A\n')
        f.write(f'cd /d "{PROJECT_DIR}"\n')
        f.write(f'\n')
        f.write(f':loop\n')
        f.write(f'echo [%date% %time%] Starting collector...\n')
        f.write(f'"{PYTHON_EXE}" -u "{MAIN_PY}" --collect-data\n')
        f.write(f'echo.\n')
        f.write(f'echo [%date% %time%] Collector stopped. Restarting in 10s...\n')
        f.write(f'timeout /t 10 /nobreak\n')
        f.write(f'goto loop\n')
    return WRAPPER_BAT


def install():
    """Instala autostart en carpeta Startup del usuario."""
    wrapper = _create_wrapper()

    # Copiar el .bat a Startup (sin /min para que la ventana sea visible)
    with open(SHORTCUT_PATH, "w") as f:
        f.write(f'@echo off\n')
        f.write(f'start "" "{wrapper}"\n')

    print(f"Autostart instalado.")
    print(f"  Shortcut: {SHORTCUT_PATH}")
    print(f"  Wrapper:  {wrapper}")
    print(f"  Log:      {LOG_FILE}")
    print(f"")
    print(f"  El collector arrancara automaticamente al iniciar sesion.")
    print(f"  Se reinicia solo si crashea (loop con 10s de espera).")
    print(f"")
    print(f"  Para iniciar ahora: python scripts/install_collector_service.py start")


def uninstall():
    """Elimina autostart."""
    if os.path.exists(SHORTCUT_PATH):
        os.remove(SHORTCUT_PATH)
        print(f"Autostart eliminado: {SHORTCUT_PATH}")
    else:
        print("No estaba instalado.")

    if os.path.exists(WRAPPER_BAT):
        os.remove(WRAPPER_BAT)


def start():
    """Inicia el collector ahora (en background, minimizado)."""
    wrapper = _create_wrapper()
    subprocess.Popen(
        f'start /min "" "{wrapper}"',
        shell=True,
        cwd=PROJECT_DIR,
    )
    print(f"Collector iniciado en background.")
    print(f"  Log: {LOG_FILE}")


def _find_collector_pids():
    """Busca PIDs del collector usando powershell (compatible Windows 11)."""
    result = subprocess.run(
        ['powershell', '-Command',
         'Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*--collect-data*" } | Select-Object -ExpandProperty ProcessId'],
        capture_output=True, text=True,
    )
    return [l.strip() for l in result.stdout.split("\n") if l.strip().isdigit()]


def stop():
    """Detiene el collector matando los procesos."""
    pids = _find_collector_pids()

    if pids:
        for pid in pids:
            subprocess.run(["taskkill", "/f", "/pid", pid], capture_output=True)
        print(f"Collector detenido (PIDs: {', '.join(pids)})")
    else:
        print("Collector no estaba corriendo.")

    # Matar el .bat wrapper si existe
    subprocess.run(
        'taskkill /f /fi "WINDOWTITLE eq BotStrike Data Collector"',
        shell=True, capture_output=True,
    )


def status():
    """Muestra estado del collector."""
    # Autostart instalado?
    if os.path.exists(SHORTCUT_PATH):
        print(f"  Autostart: INSTALADO ({SHORTCUT_PATH})")
    else:
        print(f"  Autostart: NO instalado")

    # Proceso corriendo?
    pids = _find_collector_pids()
    if pids:
        print(f"  Proceso:   ACTIVO (PID {', '.join(pids)})")
    else:
        print(f"  Proceso:   NO corriendo")

    # Ultimo dato?
    metadata_path = os.path.join(PROJECT_DIR, "data", "metadata.json")
    if os.path.exists(metadata_path):
        import json, time as _time
        with open(metadata_path) as f:
            meta = json.load(f)
        last = meta.get("last_updated", 0)
        if last:
            ago = _time.time() - last
            if ago < 120:
                print(f"  Datos:     Actualizados hace {ago:.0f}s")
            elif ago < 7200:
                print(f"  Datos:     Actualizados hace {ago/60:.0f}min")
            else:
                print(f"  Datos:     Ultima actualizacion hace {ago/3600:.1f}h")
        print(f"  Source:    {meta.get('source', '?')}")
        symbols = meta.get("symbols", [])
        for sym in symbols:
            sym_meta = meta.get(sym, {})
            trades = sym_meta.get("total_trades_today", 0)
            last_id = sym_meta.get("last_trade_id", "?")
            print(f"  {sym}: {trades} trades hoy, last_id={last_id}")

    # Ultimas lineas del log
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if lines:
            print(f"\n  Log ({LOG_FILE}):")
            for line in lines[-5:]:
                print(f"    {line.rstrip()}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()
    actions = {"install": install, "uninstall": uninstall,
               "start": start, "stop": stop, "status": status}
    if cmd in actions:
        actions[cmd]()
    else:
        print(f"Comando desconocido: {cmd}")
        print("Usa: install, uninstall, start, stop, status")
