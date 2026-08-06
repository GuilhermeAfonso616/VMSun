"""Valida o carregamento e o ciclo de vida basico do HCNetSDK Linux64.

O script nao conecta a nenhum dispositivo e nao le credenciais. O diretorio
do SDK deve ser montado no container e informado por HIK_SDK_LIB_DIR.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


def main() -> int:
    lib_dir = Path(os.environ.get("HIK_SDK_LIB_DIR", "/opt/hikvision/lib")).resolve()
    library = lib_dir / "libhcnetsdk.so"
    component_dir = lib_dir / "HCNetSDKCom"

    if not library.is_file():
        print(f"ERROR missing_library path={library}")
        return 2
    if not component_dir.is_dir():
        print(f"ERROR missing_component_dir path={component_dir}")
        return 2

    try:
        sdk = ctypes.CDLL(str(library))
    except OSError as exc:
        print(f"ERROR load_failed library={library} reason={exc}")
        return 3

    sdk.NET_DVR_Init.argtypes = []
    sdk.NET_DVR_Init.restype = ctypes.c_bool
    sdk.NET_DVR_Cleanup.argtypes = []
    sdk.NET_DVR_Cleanup.restype = ctypes.c_bool
    sdk.NET_DVR_GetSDKBuildVersion.argtypes = []
    sdk.NET_DVR_GetSDKBuildVersion.restype = ctypes.c_uint32
    sdk.NET_DVR_GetLastError.argtypes = []
    sdk.NET_DVR_GetLastError.restype = ctypes.c_uint32

    initialized = bool(sdk.NET_DVR_Init())
    if not initialized:
        print(f"ERROR init_failed sdk_error={int(sdk.NET_DVR_GetLastError())}")
        return 4

    try:
        version = int(sdk.NET_DVR_GetSDKBuildVersion())
        print(
            "OK hcnet_sdk_initialized "
            f"build_hex=0x{version:08X} library={library} components={component_dir}"
        )
        return 0
    finally:
        if not bool(sdk.NET_DVR_Cleanup()):
            print(f"WARNING cleanup_failed sdk_error={int(sdk.NET_DVR_GetLastError())}")


if __name__ == "__main__":
    raise SystemExit(main())
