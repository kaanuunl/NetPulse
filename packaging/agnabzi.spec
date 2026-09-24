# PyInstaller build definition: pyinstaller packaging/agnabzi.spec
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

analysis = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "agnabzi", "web", "static"), os.path.join("agnabzi", "web", "static"))],
    hiddenimports=[
        "agnabzi.demo",
        "agnabzi.probe",
        "agnabzi.selftest",
        "agnabzi.windows.api",
        "agnabzi.windows.autostart",
        "agnabzi.windows.etw",
        "agnabzi.windows.firewall",
        "agnabzi.windows.icons",
        "agnabzi.windows.tray",
    ],
    excludes=["tkinter", "unittest", "pydoc", "test"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    name="NetPulse",
    icon=os.path.join(ROOT, "agnabzi", "web", "static", "img", "netpulse.ico"),
    version=os.path.join(ROOT, "packaging", "version_info.txt"),
    console=False,
    upx=False,
    uac_admin=False,
)
