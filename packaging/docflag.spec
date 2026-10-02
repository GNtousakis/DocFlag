# PyInstaller spec: one-folder build of DocFlag.
#   pyinstaller packaging/docflag.spec --noconfirm
# Produces dist/DocFlag/ (DocFlag.exe + _internal/). Must be run on the
# operating system you are building for.
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# NiceGUI ships its web assets as package data that PyInstaller can't infer.
datas, binaries, hiddenimports = collect_all("nicegui")
# Only needed (and only present) on Windows, for .doc conversion via Word.
hiddenimports += ["win32com.client", "pythoncom", "pywintypes"]
datas += [(os.path.join(ROOT, "docflag", "default_words.txt"), "docflag")]

a = Analysis(
    [os.path.join(ROOT, "docflag.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "docx"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="DocFlag",
    # Keep the console: it shows the network address, and closing it is how
    # the server is stopped.
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, name="DocFlag")
