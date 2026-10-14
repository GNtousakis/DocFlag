# PyInstaller spec: one-folder build of DocFlag.
#   pyinstaller packaging/docflag.spec --noconfirm
# Produces dist/DocFlag/ (DocFlag.exe + _internal/). Must be run on the
# operating system you are building for.
import os
import sys

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# NiceGUI ships its web assets as package data that PyInstaller can't infer.
datas, binaries, hiddenimports = collect_all("nicegui")
# Only needed (and only present) on Windows, for .doc conversion via Word.
hiddenimports += ["win32com.client", "pythoncom", "pywintypes"]
# Windows OCR for the comparison tab: the pywinrt projections are native
# extension modules in a namespace package, so collect everything (as
# DocDiff's tools/build_exe.py does). `DocFlag.exe --selftest` checks it.
if sys.platform == "win32":
    for package in (
        "winrt",
        "winrt.system",
        "winrt.windows.foundation",
        "winrt.windows.foundation.collections",
        "winrt.windows.globalization",
        "winrt.windows.graphics.imaging",
        "winrt.windows.media.ocr",
        "winrt.windows.storage.streams",
    ):
        package_datas, package_binaries, package_imports = collect_all(package)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_imports + [package]
datas += [
    (os.path.join(ROOT, "docflag", name), "docflag")
    for name in ("default_words.txt", "default_replacements.txt")
]
# Canned OCR results, used when Windows OCR is unavailable.
datas += [(os.path.join(ROOT, "docflag", "compare", "fixtures"), "docflag/compare/fixtures")]

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
