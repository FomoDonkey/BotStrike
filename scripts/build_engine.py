"""
Build the BotStrike engine sidecar with PyInstaller.

Produces: desktop/src-tauri/binaries/botstrike-engine-x86_64-pc-windows-msvc.exe
(Tauri sidecar naming convention: {name}-{target_triple}.exe)

Usage:
    python scripts/build_engine.py
"""
import subprocess
import shutil
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(ROOT, "desktop", "src-tauri", "binaries")

def main():
    os.makedirs(DIST_DIR, exist_ok=True)

    # PyInstaller command — onefile mode for single exe
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--console",  # Need console for logging output
        "--name", "botstrike-engine",
        # Add all Python source directories as data
        "--add-data", f"{os.path.join(ROOT, 'config')};config",
        "--add-data", f"{os.path.join(ROOT, 'core')};core",
        "--add-data", f"{os.path.join(ROOT, 'strategies')};strategies",
        "--add-data", f"{os.path.join(ROOT, 'risk')};risk",
        "--add-data", f"{os.path.join(ROOT, 'portfolio')};portfolio",
        "--add-data", f"{os.path.join(ROOT, 'execution')};execution",
        "--add-data", f"{os.path.join(ROOT, 'exchange')};exchange",
        "--add-data", f"{os.path.join(ROOT, 'logging_metrics')};logging_metrics",
        "--add-data", f"{os.path.join(ROOT, 'trade_database')};trade_database",
        "--add-data", f"{os.path.join(ROOT, 'data_lifecycle')};data_lifecycle",
        "--add-data", f"{os.path.join(ROOT, 'analytics')};analytics",
        "--add-data", f"{os.path.join(ROOT, 'notifications')};notifications",
        "--add-data", f"{os.path.join(ROOT, 'backtesting')};backtesting",
        # NOT including data/ — it has parquet files that are huge and user-specific
        "--add-data", f"{os.path.join(ROOT, 'server')};server",
        # Hidden imports that PyInstaller might miss
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "uvicorn.lifespan.off",
        "--hidden-import", "nacl",
        "--hidden-import", "nacl.signing",
        "--hidden-import", "nacl.encoding",
        "--hidden-import", "structlog",
        "--hidden-import", "dotenv",
        # Exclude heavy packages not needed for bridge server
        "--exclude-module", "torch",
        "--exclude-module", "tensorflow",
        "--exclude-module", "llvmlite",
        "--exclude-module", "numba",
        "--exclude-module", "scipy",
        "--exclude-module", "matplotlib",
        "--exclude-module", "PIL",
        "--exclude-module", "lxml",
        "--exclude-module", "streamlit",
        "--exclude-module", "plotly",
        "--exclude-module", "lightgbm",
        "--exclude-module", "sklearn",
        "--exclude-module", "IPython",
        "--exclude-module", "notebook",
        "--exclude-module", "jupyter",
        "--exclude-module", "rich",
        # Dist path
        "--distpath", os.path.join(ROOT, "build", "engine"),
        "--workpath", os.path.join(ROOT, "build", "pyinstaller"),
        "--specpath", os.path.join(ROOT, "build"),
        # Entry point
        os.path.join(ROOT, "server", "bridge.py"),
    ]

    print("Building engine with PyInstaller...")
    print(f"  Command: {' '.join(cmd[:5])}...")
    result = subprocess.run(cmd, cwd=ROOT)

    if result.returncode != 0:
        print("ERROR: PyInstaller build failed!")
        sys.exit(1)

    # Copy the entire onedir folder to Tauri binaries
    src_dir = os.path.join(ROOT, "build", "engine", "botstrike-engine")
    src_exe = os.path.join(src_dir, "botstrike-engine.exe")
    # Tauri externalBin target
    dst_exe = os.path.join(DIST_DIR, "botstrike-engine-x86_64-pc-windows-msvc.exe")
    # Also copy the full folder for the Rust launcher to find
    dst_engine_dir = os.path.join(DIST_DIR, "engine")

    if os.path.exists(src_exe):
        # Copy exe with Tauri naming (for externalBin)
        shutil.copy2(src_exe, dst_exe)
        # Copy full folder (exe + DLLs + _internal)
        if os.path.exists(dst_engine_dir):
            shutil.rmtree(dst_engine_dir)
        shutil.copytree(src_dir, dst_engine_dir)

        size_mb = sum(
            os.path.getsize(os.path.join(dp, f))
            for dp, _, fns in os.walk(dst_engine_dir)
            for f in fns
        ) / 1024 / 1024
        print(f"\nEngine built successfully!")
        print(f"  Dir: {dst_engine_dir} ({size_mb:.0f} MB total)")
        print(f"  Exe: {dst_engine_dir}/botstrike-engine.exe")
    else:
        print(f"ERROR: Expected exe not found at {src_exe}")
        sys.exit(1)


if __name__ == "__main__":
    main()
