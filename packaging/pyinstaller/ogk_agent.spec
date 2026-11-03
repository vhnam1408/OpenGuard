# Build on Windows x64 with Python x64. The outer Inno installer is one EXE;
# the privileged service deliberately uses onedir (no temporary DLL extraction).
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).resolve().parents[1]
analysis = Analysis(
    [str(root / "packaging/pyinstaller/service_entry.py")],
    pathex=[str(root / "packages/agent/src"), str(root / "packages/contracts/src")],
    binaries=[],
    datas=collect_data_files("tzdata"),
    hiddenimports=collect_submodules("ogk_agent") + collect_submodules("ogk_contracts")
        + ["win32timezone", "servicemanager", "win32serviceutil"],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["ogk_ui", "ogk_server", "pystray", "tkinter"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True, name="ogk_agent",
    console=False, debug=False, strip=False, upx=False,
    uac_admin=False, uac_uiaccess=False, disable_windowed_traceback=True,
)
bundle = COLLECT(exe, analysis.binaries, analysis.datas,
                 strip=False, upx=False, name="ogk_agent")
