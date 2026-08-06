# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules("jinja2")
hiddenimports += collect_submodules("sqlalchemy")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("pydantic_settings")
hiddenimports += collect_submodules("ultralytics")
hiddenimports += collect_submodules("torch")
hiddenimports += collect_submodules("torchvision")
hiddenimports += collect_submodules("cv2")
hiddenimports += collect_submodules("requests")

datas = [
    ("templates", "templates"),
    ("best.pt", "."),
    (r"C:\Users\mvpro\AppData\Local\Programs\Python\Python313\Lib\site-packages\wsdl", "wsdl"),
    (".env.example", "."),
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ServidorAnalitico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="ServidorAnalitico",
)