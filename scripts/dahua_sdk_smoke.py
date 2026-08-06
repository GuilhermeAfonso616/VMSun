"""Valida carregamento e ciclo de vida básico do Dahua NetSDK Linux64."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


def main() -> int:
    lib_dir = Path(os.getenv("DAHUA_SDK_LIB_DIR", "/opt/dahua/lib")).resolve()
    library = lib_dir / "libdhnetsdk.so"
    if not library.is_file():
        print(f"ERROR missing_library path={library}")
        return 2
    try:
        sdk = ctypes.CDLL(str(library))
    except OSError as exc:
        print(f"ERROR load_failed library={library} reason={exc}")
        return 3
    sdk.CLIENT_Init.argtypes = [ctypes.c_void_p, ctypes.c_long]
    sdk.CLIENT_Init.restype = ctypes.c_int
    sdk.CLIENT_GetSDKVersion.argtypes = []
    sdk.CLIENT_GetSDKVersion.restype = ctypes.c_uint32
    sdk.CLIENT_Cleanup.argtypes = []
    sdk.CLIENT_Cleanup.restype = None
    if not sdk.CLIENT_Init(None, 0):
        print("ERROR init_failed")
        return 4
    try:
        version = int(sdk.CLIENT_GetSDKVersion())
        print(f"OK dahua_netsdk_initialized version_hex=0x{version:08X} library={library}")
        return 0
    finally:
        sdk.CLIENT_Cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
