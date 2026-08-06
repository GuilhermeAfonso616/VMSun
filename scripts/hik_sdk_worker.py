"""Worker isolado para operacoes curtas com o Hikvision HCNetSDK no Linux."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


BOOL = ctypes.c_int32
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
WORD = ctypes.c_uint16


class WorkerError(RuntimeError):
    pass


class NET_DVR_JPEGPARA(ctypes.Structure):
    _fields_ = [("wPicSize", WORD), ("wPicQuality", WORD)]


class NET_DVR_LOCAL_SDK_PATH(ctypes.Structure):
    _fields_ = [("sPath", ctypes.c_char * 256), ("byRes", ctypes.c_ubyte * 128)]


class NET_DVR_POINT_FRAME(ctypes.Structure):
    _fields_ = [
        ("xTop", ctypes.c_int32),
        ("yTop", ctypes.c_int32),
        ("xBottom", ctypes.c_int32),
        ("yBottom", ctypes.c_int32),
        ("bCounter", ctypes.c_int32),
    ]


def _payload() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise WorkerError("Requisicao interna do SDK excedeu o limite.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerError("Requisicao interna do SDK invalida.") from exc
    if not isinstance(value, dict):
        raise WorkerError("Requisicao interna do SDK invalida.")
    return value


def _required(payload: dict[str, Any], name: str, maximum: int = 256) -> str:
    value = str(payload.get(name, ""))
    if not value or len(value) > maximum or "\x00" in value:
        raise WorkerError(f"Campo {name} invalido.")
    return value


def _load_library() -> tuple[ctypes.CDLL, Path]:
    lib_dir = Path(os.getenv("HIK_SDK_LIB_DIR", "/opt/hikvision/lib")).resolve()
    library_path = lib_dir / "libhcnetsdk.so"
    if not library_path.is_file():
        raise WorkerError("libhcnetsdk.so nao foi encontrada na instalacao validada.")
    try:
        library = ctypes.CDLL(str(library_path), mode=ctypes.RTLD_GLOBAL)
    except OSError:
        for dependency in sorted(lib_dir.rglob("*.so*")):
            if dependency == library_path or not dependency.is_file():
                continue
            try:
                ctypes.CDLL(str(dependency), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass
        try:
            library = ctypes.CDLL(str(library_path), mode=ctypes.RTLD_GLOBAL)
        except OSError as exc:
            raise WorkerError(f"HCNetSDK nao carregou: {exc}") from exc
    return library, lib_dir


def _configure(library: ctypes.CDLL, lib_dir: Path) -> None:
    if hasattr(library, "NET_DVR_SetSDKInitCfg"):
        sdk_path = NET_DVR_LOCAL_SDK_PATH()
        encoded = (str(lib_dir) + os.sep).encode("utf-8")
        if len(encoded) < 256:
            sdk_path.sPath = encoded
            library.NET_DVR_SetSDKInitCfg.argtypes = [ctypes.c_int32, ctypes.c_void_p]
            library.NET_DVR_SetSDKInitCfg.restype = BOOL
            library.NET_DVR_SetSDKInitCfg(2, ctypes.byref(sdk_path))
    library.NET_DVR_Init.argtypes = []
    library.NET_DVR_Init.restype = BOOL
    if not library.NET_DVR_Init():
        raise WorkerError("HCNetSDK recusou a inicializacao.")
    if hasattr(library, "NET_DVR_SetConnectTime"):
        library.NET_DVR_SetConnectTime.argtypes = [DWORD, DWORD]
        library.NET_DVR_SetConnectTime(5000, 1)


def _last_error(library: ctypes.CDLL) -> int:
    library.NET_DVR_GetLastError.argtypes = []
    library.NET_DVR_GetLastError.restype = DWORD
    return int(library.NET_DVR_GetLastError())


def _login(library: ctypes.CDLL, payload: dict[str, Any]) -> tuple[int, bytes]:
    host = _required(payload, "host", 64).encode("ascii")
    username = _required(payload, "username", 63).encode("utf-8")
    password = _required(payload, "password", 127).encode("utf-8")
    port = int(payload.get("port", 8000))
    if not 1 <= port <= 65535:
        raise WorkerError("Porta SDK invalida.")
    device_info = ctypes.create_string_buffer(512)
    library.NET_DVR_Login_V30.argtypes = [
        ctypes.c_char_p, WORD, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
    ]
    library.NET_DVR_Login_V30.restype = LONG
    user_id = int(library.NET_DVR_Login_V30(host, port, username, password, device_info))
    if user_id < 0:
        raise WorkerError(f"Login HCNetSDK recusado (codigo {_last_error(library)}).")
    return user_id, bytes(device_info)


def _sdk_version(library: ctypes.CDLL) -> str:
    if not hasattr(library, "NET_DVR_GetSDKBuildVersion"):
        return ""
    library.NET_DVR_GetSDKBuildVersion.argtypes = []
    library.NET_DVR_GetSDKBuildVersion.restype = DWORD
    version = int(library.NET_DVR_GetSDKBuildVersion())
    return f"{(version >> 24) & 0xff}.{(version >> 16) & 0xff}.{version & 0xffff}"


def _channel_metadata(raw_info: bytes, requested_channel: int) -> dict[str, int]:
    analog_channels = raw_info[52] if len(raw_info) > 52 else 0
    analog_start = raw_info[53] if len(raw_info) > 53 else 1
    digital_channels = raw_info[55] if len(raw_info) > 55 else 0
    digital_start = raw_info[66] if len(raw_info) > 66 else 0
    if len(raw_info) > 68:
        digital_channels += raw_info[68] * 256

    sdk_channel = requested_channel
    # Na interface, canal 1 significa D1/primeiro canal do NVR. O HCNetSDK,
    # porem, normalmente numera esse mesmo canal a partir de byStartDChan (33).
    if digital_channels > 0 and digital_start > 0 and requested_channel < digital_start:
        sdk_channel = digital_start + requested_channel - 1
    return {
        "analog_channels": analog_channels,
        "analog_channel_start": analog_start,
        "digital_channels": digital_channels,
        "digital_channel_start": digital_start,
        "sdk_channel": sdk_channel,
    }


def _inspect(library: ctypes.CDLL, raw_info: bytes, requested_channel: int) -> dict[str, object]:
    serial = raw_info[:48].split(b"\0", 1)[0].decode("utf-8", "replace").strip()
    return {
        "serial_number": serial,
        "sdk_build": _sdk_version(library),
        "ptz_capability_verified": True,
        "ptz_capable": True,
        "ptz_pan": True,
        "ptz_zoom": True,
        **_channel_metadata(raw_info, requested_channel),
    }


def _snapshot(library: ctypes.CDLL, user_id: int, payload: dict[str, Any]) -> None:
    output = Path(_required(payload, "output_file", 1024))
    channel = int(payload.get("channel", 1))
    if not 1 <= channel <= 4096:
        raise WorkerError("Canal invalido.")
    params = NET_DVR_JPEGPARA(0xFF, 2)
    library.NET_DVR_CaptureJPEGPicture.argtypes = [LONG, LONG, ctypes.POINTER(NET_DVR_JPEGPARA), ctypes.c_char_p]
    library.NET_DVR_CaptureJPEGPicture.restype = BOOL
    if not library.NET_DVR_CaptureJPEGPicture(user_id, channel, ctypes.byref(params), os.fsencode(output)):
        raise WorkerError(f"Captura HCNetSDK recusada (codigo {_last_error(library)}).")


def _ptz_commands(payload: dict[str, Any]) -> list[int]:
    pan, tilt, zoom = (int(payload.get(name, 0)) for name in ("pan", "tilt", "zoom"))
    commands: list[int] = []
    diagonal_commands = {(-1, 1): 25, (1, 1): 26, (-1, -1): 27, (1, -1): 28}
    if pan and tilt:
        commands.append(diagonal_commands[(max(-1, min(1, pan)), max(-1, min(1, tilt)))])
    else:
        if pan < 0:
            commands.append(23)
        elif pan > 0:
            commands.append(24)
        if tilt > 0:
            commands.append(21)
        elif tilt < 0:
            commands.append(22)
    if zoom > 0:
        commands.append(11)
    elif zoom < 0:
        commands.append(12)
    return commands


def _ptz_control_func(library: ctypes.CDLL):
    control = library.NET_DVR_PTZControlWithSpeed_Other
    control.argtypes = [LONG, LONG, DWORD, DWORD, DWORD]
    control.restype = BOOL
    return control


def _ptz(library: ctypes.CDLL, user_id: int, payload: dict[str, Any]) -> None:
    """Pulso limitado: start, espera duration_ms, stop. Mantido para
    equipamentos/versoes que nao aceitem o par ptz_start/ptz_stop."""
    channel = int(payload.get("channel", 1))
    speed = max(1, min(7, int(payload.get("speed", 4))))
    duration = max(80, min(800, int(payload.get("duration_ms", 300)))) / 1000.0
    commands = _ptz_commands(payload)
    if not commands:
        return
    control = _ptz_control_func(library)
    started: list[int] = []
    try:
        for command in commands:
            if not control(user_id, channel, command, 0, speed):
                error = _last_error(library)
                if error == 10:
                    raise WorkerError(
                        f"PTZ HCNetSDK sem resposta no canal SDK {channel} (codigo 10)."
                    )
                raise WorkerError(f"PTZ HCNetSDK recusado (codigo {error}, canal SDK {channel}).")
            started.append(command)
        time.sleep(duration)
    finally:
        for command in reversed(started):
            control(user_id, channel, command, 1, speed)


def _ptz_start(library: ctypes.CDLL, user_id: int, payload: dict[str, Any]) -> list[int]:
    """Inicia o movimento sem parar sozinho; o chamador deve usar _ptz_stop."""
    channel = int(payload.get("channel", 1))
    speed = max(1, min(7, int(payload.get("speed", 4))))
    commands = _ptz_commands(payload)
    if not commands:
        return []
    control = _ptz_control_func(library)
    started: list[int] = []
    for command in commands:
        if not control(user_id, channel, command, 0, speed):
            error = _last_error(library)
            for done in reversed(started):
                control(user_id, channel, done, 1, speed)
            raise WorkerError(f"PTZ HCNetSDK recusado (codigo {error}, canal SDK {channel}).")
        started.append(command)
    return started


def _ptz_stop(library: ctypes.CDLL, user_id: int, payload: dict[str, Any]) -> None:
    channel = int(payload.get("channel", 1))
    speed = max(1, min(7, int(payload.get("speed", 4))))
    commands = [int(item) for item in (payload.get("commands") or [])]
    if not commands:
        return
    control = _ptz_control_func(library)
    for command in reversed(commands):
        control(user_id, channel, command, 1, speed)


def _ptz_3d(library: ctypes.CDLL, user_id: int, payload: dict[str, Any]) -> None:
    """Posicionamento 3D Hikvision; POINT_FRAME usa coordenadas 0..255."""

    channel = int(payload.get("channel", 1))
    coordinates = [
        max(0, min(255, int(payload.get(name, 0))))
        for name in ("x_start", "y_start", "x_end", "y_end")
    ]
    frame = NET_DVR_POINT_FRAME(*coordinates, 0)
    position = getattr(library, "NET_DVR_PTZSelZoomIn_EX", None)
    if position is None:
        raise WorkerError("Esta versao do HCNetSDK nao oferece posicionamento 3D.")
    position.argtypes = [LONG, LONG, ctypes.POINTER(NET_DVR_POINT_FRAME)]
    position.restype = BOOL
    if not position(user_id, channel, ctypes.byref(frame)):
        raise WorkerError(
            f"Posicionamento 3D HCNetSDK recusado "
            f"(codigo {_last_error(library)}, canal SDK {channel})."
        )


def _goto_preset(library: ctypes.CDLL, user_id: int, payload: dict[str, Any]) -> None:
    channel = int(payload.get("channel", 1))
    try:
        preset = int(payload.get("preset_token"))
    except (TypeError, ValueError) as exc:
        raise WorkerError("Numero do preset Hikvision invalido.") from exc
    if not 1 <= preset <= 300:
        raise WorkerError("O preset Hikvision deve estar entre 1 e 300.")
    preset_control = getattr(library, "NET_DVR_PTZPreset_Other", None)
    if preset_control is None:
        raise WorkerError("Esta versao do HCNetSDK nao oferece acionamento de preset.")
    preset_control.argtypes = [LONG, LONG, DWORD, DWORD]
    preset_control.restype = BOOL
    # GOTO_PRESET = 39. Nao grava a posicao atual.
    if not preset_control(user_id, channel, 39, preset):
        raise WorkerError(
            f"Acionamento do preset HCNetSDK recusado "
            f"(codigo {_last_error(library)}, canal SDK {channel})."
        )


class NET_DVR_XML_CONFIG_INPUT(ctypes.Structure):
    _fields_ = [
        ("dwSize", DWORD),
        ("lpRequestUrl", ctypes.c_void_p),
        ("dwRequestUrlSize", DWORD),
        ("lpInBuffer", ctypes.c_void_p),
        ("dwInBufferSize", DWORD),
        ("dwRecvTimeOut", DWORD),
        ("byRes", ctypes.c_ubyte * 32),
    ]


class NET_DVR_XML_CONFIG_OUTPUT(ctypes.Structure):
    _fields_ = [
        ("dwSize", DWORD),
        ("lpOutBuffer", ctypes.c_void_p),
        ("dwOutBufferSize", DWORD),
        ("dwReturnedXMLSize", DWORD),
        ("lpStatusBuffer", ctypes.c_void_p),
        ("dwStatusSize", DWORD),
        ("byRes", ctypes.c_ubyte * 32),
    ]


def _parse_hik_preset_xml(xml_data: str) -> list[dict[str, str]]:
    presets: list[dict[str, str]] = []
    try:
        root = ET.fromstring(xml_data)
        for elem in root.iter():
            tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag_name == "PTZPreset":
                token = ""
                name = ""
                for child in elem:
                    ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if ctag == "id":
                        token = str(child.text or "").strip()
                    elif ctag in ("presetName", "name"):
                        name = str(child.text or "").strip()
                if token:
                    presets.append({
                        "token": token,
                        "name": name or f"Preset {token}",
                    })
    except Exception:
        pass
    return presets


def _list_presets_isapi_http(payload: dict[str, Any], sdk_channel: int, requested_channel: int) -> list[dict[str, str]]:
    host = payload.get("host")
    if not host:
        return []
    username = payload.get("username", "admin")
    password = payload.get("password", "")
    ports = [80, int(payload.get("port", 8000)), 8080]
    channels = [sdk_channel, requested_channel]
    try:
        import requests
        from requests.auth import HTTPBasicAuth, HTTPDigestAuth
        for port in ports:
            for ch in channels:
                url = f"http://{host}:{port}/ISAPI/PTZCtrl/channels/{ch}/presets"
                try:
                    resp = requests.get(url, auth=HTTPDigestAuth(username, password), timeout=3)
                    if resp.status_code == 401:
                        resp = requests.get(url, auth=HTTPBasicAuth(username, password), timeout=3)
                    if resp.status_code == 200 and "<PTZPreset" in resp.text:
                        presets = _parse_hik_preset_xml(resp.text)
                        if presets:
                            return presets
                except Exception:
                    continue
    except ImportError:
        pass
    return []


def _list_presets(library: ctypes.CDLL, user_id: int, payload: dict[str, Any], raw_info: bytes) -> list[dict[str, str]]:
    requested_channel = int(payload.get("channel", 1))
    meta = _channel_metadata(raw_info, requested_channel)
    sdk_channel = meta.get("sdk_channel", requested_channel)

    std_xml = getattr(library, "NET_DVR_STDXMLConfig", None)
    if std_xml is not None:
        urls_to_try = [
            f"GET /ISAPI/PTZCtrl/channels/{sdk_channel}/presets\r\n",
            f"GET /ISAPI/PTZCtrl/channels/{requested_channel}/presets\r\n",
        ]
        out_buf = ctypes.create_string_buffer(64 * 1024)
        status_buf = ctypes.create_string_buffer(4 * 1024)
        std_xml.argtypes = [LONG, ctypes.POINTER(NET_DVR_XML_CONFIG_INPUT), ctypes.POINTER(NET_DVR_XML_CONFIG_OUTPUT)]
        std_xml.restype = BOOL

        for url_str in urls_to_try:
            url_bytes = url_str.encode("utf-8")
            inp = NET_DVR_XML_CONFIG_INPUT()
            inp.dwSize = ctypes.sizeof(NET_DVR_XML_CONFIG_INPUT)
            inp.lpRequestUrl = ctypes.cast(ctypes.c_char_p(url_bytes), ctypes.c_void_p)
            inp.dwRequestUrlSize = len(url_bytes)
            inp.dwRecvTimeOut = 5000

            outp = NET_DVR_XML_CONFIG_OUTPUT()
            outp.dwSize = ctypes.sizeof(NET_DVR_XML_CONFIG_OUTPUT)
            outp.lpOutBuffer = ctypes.cast(out_buf, ctypes.c_void_p)
            outp.dwOutBufferSize = len(out_buf)
            outp.lpStatusBuffer = ctypes.cast(status_buf, ctypes.c_void_p)
            outp.dwStatusSize = len(status_buf)

            if std_xml(user_id, ctypes.byref(inp), ctypes.byref(outp)):
                ret_size = int(outp.dwReturnedXMLSize)
                if ret_size > 0:
                    xml_text = out_buf.raw[:ret_size].decode("utf-8", "ignore")
                else:
                    xml_text = out_buf.value.decode("utf-8", "ignore")
                if "<PTZPreset" in xml_text:
                    presets = _parse_hik_preset_xml(xml_text)
                    if presets:
                        return presets

    return _list_presets_isapi_http(payload, sdk_channel, requested_channel)


def _write_result(result_path: Path, result: dict[str, object]) -> None:
    """Grava atomicamente (tmp + rename) para o pai poder ler o arquivo
    enquanto este processo ainda esta rodando (caso do ptz_start, que so
    sai depois do stop)."""
    tmp_path = result_path.with_suffix(result_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")
    os.replace(tmp_path, result_path)


def _stop_signal_path(result_path: Path) -> Path:
    return result_path.with_suffix(result_path.suffix + ".stop")


def _command_signal_path(result_path: Path) -> Path:
    return result_path.with_suffix(result_path.suffix + ".cmd")


def _vector_of(payload: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        max(-1, min(1, int(payload.get("pan", 0)))),
        max(-1, min(1, int(payload.get("tilt", 0)))),
        max(-1, min(1, int(payload.get("zoom", 0)))),
        max(1, min(7, int(payload.get("speed", 4)))),
    )


def _read_command_vector(command_signal_path: Path) -> tuple[int, int, int, int] | None:
    """Le a ultima direcao/velocidade pedida pelo pai (arraste continuo do
    joystick). Retorna (pan, tilt, zoom, speed) ou None se ainda nao ha uma
    atualizacao legivel. O pai grava esse arquivo de forma atomica, entao aqui
    so precisamos tolerar leituras vazias/parciais entre o tmp e o rename."""
    try:
        text = command_signal_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    try:
        return _vector_of(value)
    except (TypeError, ValueError):
        return None


def _hold_ptz(
    library: ctypes.CDLL,
    user_id: int,
    payload: dict[str, Any],
    commands: list[int],
    result_path: Path,
    max_hold: float,
) -> None:
    """Mantem o motor girando enquanto o pai nao sinaliza stop, aplicando
    mudancas de direcao/velocidade (arraste do joystick) na MESMA sessao de
    login. Sem isso, o hold ficava preso na direcao/velocidade inicial do
    gesto: girar o joystick sem soltar nao mudava nada -- a camera ia pro lado
    errado ou lenta, so as setas (que soltam entre toques) funcionavam.

    Usa arquivos em vez do stdin porque o stdin.buffer.read() do _payload() so
    retorna com EOF -- manter o pipe aberto travaria essa leitura inicial. O
    ``.stop`` encerra; o ``.cmd`` traz a nova direcao e, a cada atualizacao,
    renova o teto de seguranca para o gesto nao cair no meio do uso."""
    stop_signal_path = _stop_signal_path(result_path)
    command_signal_path = _command_signal_path(result_path)
    current_commands = commands
    current_payload = payload
    current_vector = _vector_of(payload)
    last_mtime = 0.0
    deadline = time.time() + max(1.0, max_hold)
    try:
        while time.time() < deadline:
            if stop_signal_path.exists():
                return
            try:
                mtime = command_signal_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            if mtime and mtime != last_mtime:
                last_mtime = mtime
                # Cada comando do pai (joystick ainda pressionado) renova o teto.
                deadline = time.time() + max(1.0, max_hold)
                vector = _read_command_vector(command_signal_path)
                if vector is not None and vector != current_vector:
                    _ptz_stop(library, user_id, {**current_payload, "commands": current_commands})
                    pan, tilt, zoom, speed = vector
                    current_payload = {**payload, "pan": pan, "tilt": tilt, "zoom": zoom, "speed": speed}
                    current_vector = vector
                    try:
                        current_commands = _ptz_start(library, user_id, current_payload)
                    except WorkerError:
                        # Direcao recusada pelo equipamento: fica parado, mas
                        # mantem a sessao viva para a proxima atualizacao/stop.
                        current_commands = []
            time.sleep(0.05)
    finally:
        _ptz_stop(library, user_id, {**current_payload, "commands": current_commands})


def run(action: str, payload: dict[str, Any], result_path: Path) -> dict[str, object] | None:
    """Retorna o dict de resultado para o chamador (main) gravar, ou None se
    esta funcao ja gravou o resultado ela mesma (caso do ptz_start, que
    precisa publicar o resultado antes de continuar rodando)."""
    if action not in {
        "inspect", "snapshot", "ptz", "ptz_start", "ptz_stop", "ptz_3d",
        "goto_preset", "list_presets",
    }:
        raise WorkerError("Acao SDK invalida.")
    library, lib_dir = _load_library()
    initialized = False
    user_id = -1
    try:
        _configure(library, lib_dir)
        initialized = True
        user_id, raw_info = _login(library, payload)
        if action == "inspect":
            return {
                "ok": True,
                "device": _inspect(library, raw_info, int(payload.get("channel", 1))),
            }
        if action == "snapshot":
            _snapshot(library, user_id, payload)
        elif action == "ptz":
            _ptz(library, user_id, payload)
        elif action == "ptz_start":
            commands = _ptz_start(library, user_id, payload)
            _write_result(result_path, {"ok": True, "commands": commands})
            max_hold = max(5.0, min(120.0, float(payload.get("max_hold_seconds", 90))))
            _hold_ptz(library, user_id, payload, commands, result_path, max_hold)
            return None
        elif action == "ptz_3d":
            _ptz_3d(library, user_id, payload)
        elif action == "goto_preset":
            _goto_preset(library, user_id, payload)
        elif action == "list_presets":
            presets = _list_presets(library, user_id, payload, raw_info)
            return {"ok": True, "presets": presets}
        else:
            _ptz_stop(library, user_id, payload)
        return {"ok": True}
    finally:
        if user_id >= 0:
            library.NET_DVR_Logout.argtypes = [LONG]
            library.NET_DVR_Logout(user_id)
        if initialized:
            library.NET_DVR_Cleanup.argtypes = []
            library.NET_DVR_Cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("action")
    parser.add_argument("--result-file", required=True)
    args = parser.parse_args()
    result_path = Path(args.result_file)
    try:
        result = run(args.action, _payload(), result_path)
    except Exception as exc:
        result = {"ok": False, "error": str(exc) if isinstance(exc, WorkerError) else "Falha nativa no HCNetSDK."}
    if result is None:
        return 0
    _write_result(result_path, result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
