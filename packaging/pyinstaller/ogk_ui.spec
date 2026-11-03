from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).resolve().parents[1]
analysis = Analysis(
    [str(root / "packaging/pyinstaller/tray_entry.py")],
    pathex=[str(root / "packages/interfaces/src"), str(root / "packages/contracts/src")],
    binaries=[],
    datas=collect_data_files("ogk_ui", includes=["tray/assets/*"]),
    hiddenimports=collect_submodules("ogk_ui.tray") + collect_submodules("ogk_contracts")
        + ["pystray._win32", "PIL.Image", "PIL.ImageDraw", "tkinter", "win32timezone"],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["ogk_agent", "ogk_server"], noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True, name="ogk_ui",
    console=False, debug=False, strip=False, upx=False,
    uac_admin=False, uac_uiaccess=False, disable_windowed_traceback=True,
)
bundle = COLLECT(exe, analysis.binaries, analysis.datas,
                 strip=False, upx=False, name="ogk_ui")
