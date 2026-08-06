"""Worker isolado para operacoes curtas com o Dahua NetSDK no Linux."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any


BOOL = ctypes.c_int32
DWORD = ctypes.c_uint32
LLONG = ctypes.c_int64
WORD = ctypes.c_uint16
DISCONNECT_CALLBACK = ctypes.CFUNCTYPE(None, LLONG, ctypes.c_char_p, ctypes.c_int32, ctypes.c_void_p)
SNAP_CALLBACK = ctypes.CFUNCTYPE(None, LLONG, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, DWORD, ctypes.c_void_p)
DH_DEVSTATE_PTZ_PRESET_LIST = 0x57
DH_EXTPTZ_FASTGOTO = 0x33
PTZ_PRESET_MAX_COUNT = 300
# O NetSDK Linux atual usa NET_PTZ_PRESET com 392 bytes. Os campos usados
# aqui permanecem no inicio: nIndex (4 bytes) e szName (64 bytes).
PTZ_PRESET_RECORD_SIZE = 392


class WorkerError(RuntimeError):
    pass


class SNAP_PARAMS(ctypes.Structure):
    _fields_ = [
        ("Channel", ctypes.c_uint32),
        ("Quality", ctypes.c_uint32),
        ("ImageSize", ctypes.c_uint32),
        ("mode", ctypes.c_uint32),
        ("InterSnap", ctypes.c_uint32),
        ("CmdSerial", ctypes.c_uint32),
        ("Reserved", ctypes.c_uint32 * 4),
    ]


class NET_PTZ_PRESET_LIST(ctypes.Structure):
    _fields_ = [
        ("dwSize", DWORD),
        ("dwMaxPresetNum", DWORD),
        ("dwRetPresetNum", DWORD),
        ("pstuPtzPorsetList", ctypes.c_void_p),
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


def _load_library() -> ctypes.CDLL:
    lib_dir = Path(os.getenv("DAHUA_SDK_LIB_DIR", "/opt/dahua/lib")).resolve()
    library_path = lib_dir / "libdhnetsdk.so"
    if not library_path.is_file():
        raise WorkerError("libdhnetsdk.so nao foi encontrada na instalacao validada.")
    try:
        return ctypes.CDLL(str(library_path), mode=ctypes.RTLD_GLOBAL)
    except OSError:
        for dependency in sorted(lib_dir.rglob("*.so*")):
            if dependency == library_path or not dependency.is_file():
                continue
            try:
                ctypes.CDLL(str(dependency), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass
        try:
            return ctypes.CDLL(str(library_path), mode=ctypes.RTLD_GLOBAL)
        except OSError as exc:
            raise WorkerError(f"Dahua NetSDK nao carregou: {exc}") from exc


def _last_error(library: ctypes.CDLL) -> int:
    library.CLIENT_GetLastError.argtypes = []
    library.CLIENT_GetLastError.restype = DWORD
    return int(library.CLIENT_GetLastError())


def _login(library: ctypes.CDLL, payload: dict[str, Any]) -> tuple[int, bytes, object]:
    disconnect = DISCONNECT_CALLBACK(lambda *_args: None)
    library.CLIENT_Init.argtypes = [DISCONNECT_CALLBACK, ctypes.c_void_p]
    library.CLIENT_Init.restype = BOOL
    if not library.CLIENT_Init(disconnect, None):
        raise WorkerError("Dahua NetSDK recusou a inicializacao.")
    if hasattr(library, "CLIENT_SetConnectTime"):
        library.CLIENT_SetConnectTime.argtypes = [ctypes.c_int32, ctypes.c_int32]
        library.CLIENT_SetConnectTime(5000, 1)

    host = _required(payload, "host", 64).encode("ascii")
    username = _required(payload, "username", 63).encode("utf-8")
    password = _required(payload, "password", 127).encode("utf-8")
    port = int(payload.get("port", 37777))
    if not 1 <= port <= 65535:
        raise WorkerError("Porta SDK invalida.")
    device_info = ctypes.create_string_buffer(1024)
    error = ctypes.c_int32()
    library.CLIENT_LoginEx2.argtypes = [
        ctypes.c_char_p, WORD, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int32,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32),
    ]
    library.CLIENT_LoginEx2.restype = LLONG
    login_id = int(library.CLIENT_LoginEx2(
        host, port, username, password, 0, None, device_info, ctypes.byref(error),
    ))
    if not login_id:
        raise WorkerError(f"Login Dahua NetSDK recusado (codigo {int(error.value) or _last_error(library)}).")
    return login_id, bytes(device_info), disconnect


def _sdk_version(library: ctypes.CDLL) -> str:
    if not hasattr(library, "CLIENT_GetSDKVersion"):
        return ""
    library.CLIENT_GetSDKVersion.argtypes = []
    library.CLIENT_GetSDKVersion.restype = DWORD
    version = int(library.CLIENT_GetSDKVersion())
    return f"{(version >> 24) & 0xff}.{(version >> 16) & 0xff}.{version & 0xffff}"


def _query_system_info(
    library: ctypes.CDLL,
    login_id: int,
    command: str,
    channel: int,
) -> dict[str, Any] | None:
    """Consulta capacidades JSON sem confundir o protocolo do NVR com a câmera.

    Em NVRs Dahua, ``ptz.getCurrentProtocolCaps`` pode anunciar o protocolo
    DH-SD mesmo para um canal ligado a uma câmera fixa. Por isso a decisão PTZ
    combina esse retorno com as capacidades motorizadas da entrada de vídeo.
    """

    query = getattr(library, "CLIENT_QueryNewSystemInfo", None)
    if query is None:
        return None
    output = ctypes.create_string_buffer(256 * 1024)
    returned = ctypes.c_int32()
    query.argtypes = [
        LLONG,
        ctypes.c_char_p,
        ctypes.c_int32,
        ctypes.c_char_p,
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
        ctypes.c_int32,
    ]
    query.restype = BOOL
    if not query(
        login_id,
        command.encode("ascii"),
        max(0, int(channel)),
        output,
        len(output),
        ctypes.byref(returned),
        5000,
    ):
        return None
    try:
        payload = json.loads(output.value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not payload.get("result"):
        return None
    caps = (payload.get("params") or {}).get("caps")
    return caps if isinstance(caps, dict) else None


def _derive_ptz_capability(
    ptz_caps: dict[str, Any] | None,
    video_input_caps: dict[str, Any] | None,
) -> dict[str, object]:
    ptz_caps = ptz_caps or {}
    video_input_caps = video_input_caps or {}

    def capability(name: str) -> bool:
        value = ptz_caps.get(name)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    protocol_supports_motion = any(
        capability(name)
        for name in ("Pan", "Tile", "MoveRelatively", "Zoom")
    )
    # A presença de foco motorizado é uma propriedade do dispositivo no canal,
    # enquanto o nome/capacidade do protocolo PTZ pode ser apenas a configuração
    # genérica do NVR. Uma câmera PTZ real precisa de mecanismo de foco.
    motorized_input = any(
        bool(video_input_caps.get(name))
        for name in ("ElectricFocus", "AutofocusPeak", "SyncFocus")
    )
    # Foco motorizado tambem existe em cameras fixas. A flag bPtzDevice e a
    # evidencia especifica do NetSDK de que existe equipamento PTZ no canal.
    physical_ptz = any(
        capability(name)
        for name in ("PtzDevice", "PTZDevice", "bPtzDevice", "HasPtzDevice")
    )
    capable = protocol_supports_motion and physical_ptz
    return {
        "ptz_capable": capable,
        "ptz_capability_verified": True,
        # Preserve a evidencia de protocolo separada da flag fisica. Alguns
        # NVRs Dahua nao devolvem PtzDevice por canal; o monitor pode combinar
        # este sinal com uma classificacao explicita da camera como PTZ sem
        # transformar todos os canais fixos do NVR em PTZ.
        "ptz_protocol_motion": protocol_supports_motion,
        "ptz_protocol": str(ptz_caps.get("Name") or ""),
        "ptz_pan": capable and bool(capability("Pan") or capability("Tile")),
        "ptz_zoom": capable and capability("Zoom"),
        "physical_ptz": physical_ptz,
        "motorized_input": motorized_input,
    }


def _inspect(
    library: ctypes.CDLL,
    login_id: int,
    raw_info: bytes,
    requested_channel: int,
) -> dict[str, object]:
    serial = raw_info[:48].split(b"\0", 1)[0].decode("utf-8", "replace").strip()
    channels = int.from_bytes(raw_info[64:68], byteorder=sys.byteorder, signed=True) if len(raw_info) >= 68 else 0
    sdk_channel = max(0, int(requested_channel) - 1)
    ptz_caps = _query_system_info(
        library,
        login_id,
        "ptz.getCurrentProtocolCaps",
        sdk_channel,
    )
    video_input_caps = _query_system_info(
        library,
        login_id,
        "devVideoInput.getCaps",
        sdk_channel,
    )
    return {
        "serial_number": serial,
        "digital_channels": max(0, channels),
        "sdk_build": _sdk_version(library),
        "ptz_protocol_caps": ptz_caps or {},
        "video_input_caps": video_input_caps or {},
        **_derive_ptz_capability(ptz_caps, video_input_caps),
    }


def _snapshot(library: ctypes.CDLL, login_id: int, payload: dict[str, Any]) -> None:
    output = Path(_required(payload, "output_file", 1024))
    channel = max(0, int(payload.get("channel", 1)) - 1)
    event = threading.Event()
    received: dict[str, bytes] = {}

    def receive(_login: int, buffer: int, length: int, _encoding: int, _serial: int, _user: object) -> None:
        if buffer and length and length <= 32 * 1024 * 1024:
            received["image"] = ctypes.string_at(buffer, length)
        event.set()

    callback = SNAP_CALLBACK(receive)
    library.CLIENT_SetSnapRevCallBack.argtypes = [SNAP_CALLBACK, ctypes.c_void_p]
    library.CLIENT_SetSnapRevCallBack.restype = None
    library.CLIENT_SetSnapRevCallBack(callback, None)
    params = SNAP_PARAMS(Channel=channel, Quality=3, ImageSize=0, mode=0, InterSnap=0, CmdSerial=int(time.time()) & 0x7FFFFFFF)
    library.CLIENT_SnapPictureEx.argtypes = [LLONG, ctypes.POINTER(SNAP_PARAMS), ctypes.c_void_p]
    library.CLIENT_SnapPictureEx.restype = BOOL
    if not library.CLIENT_SnapPictureEx(login_id, ctypes.byref(params), None):
        raise WorkerError(f"Captura Dahua recusada (codigo {_last_error(library)}).")
    if not event.wait(12):
        raise WorkerError("Tempo limite ao aguardar a captura Dahua.")
    image = received.get("image", b"")
    if not image.startswith(b"\xff\xd8"):
        raise WorkerError("Dahua NetSDK nao retornou uma imagem JPEG valida.")
    output.write_bytes(image)


def _ptz_commands(payload: dict[str, Any]) -> list[int]:
    pan, tilt, zoom = (int(payload.get(name, 0)) for name in ("pan", "tilt", "zoom"))
    commands: list[int] = []
    if pan < 0:
        commands.append(2)
    elif pan > 0:
        commands.append(3)
    if tilt > 0:
        commands.append(0)
    elif tilt < 0:
        commands.append(1)
    if zoom > 0:
        commands.append(4)
    elif zoom < 0:
        commands.append(5)
    return commands


def _ptz_control_func(library: ctypes.CDLL):
    control = getattr(library, "CLIENT_DHPTZControlEx2", None)
    if control is None:
        raise WorkerError("Esta versao do Dahua NetSDK nao oferece PTZControlEx2.")
    control.argtypes = [LLONG, ctypes.c_int32, ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32,
                        ctypes.c_int32, BOOL, ctypes.c_void_p]
    control.restype = BOOL
    return control


def _ptz(library: ctypes.CDLL, login_id: int, payload: dict[str, Any]) -> None:
    """Pulso limitado: start, espera duration_ms, stop. Mantido para
    equipamentos/versoes que nao aceitem o par ptz_start/ptz_stop."""
    channel = max(0, int(payload.get("channel", 1)) - 1)
    speed = max(1, min(7, int(payload.get("speed", 4))))
    duration = max(80, min(800, int(payload.get("duration_ms", 300)))) / 1000.0
    commands = _ptz_commands(payload)
    if not commands:
        return
    control = _ptz_control_func(library)
    started: list[int] = []
    try:
        for command in commands:
            if not control(login_id, channel, command, 0, speed, 0, 0, None):
                raise WorkerError(f"PTZ Dahua recusado (codigo {_last_error(library)}).")
            started.append(command)
        time.sleep(duration)
    finally:
        for command in reversed(started):
            control(login_id, channel, command, 0, speed, 0, 1, None)


def _ptz_start(library: ctypes.CDLL, login_id: int, payload: dict[str, Any]) -> list[int]:
    """Inicia o movimento sem parar sozinho; o chamador deve usar _ptz_stop."""
    channel = max(0, int(payload.get("channel", 1)) - 1)
    speed = max(1, min(7, int(payload.get("speed", 4))))
    commands = _ptz_commands(payload)
    if not commands:
        return []
    control = _ptz_control_func(library)
    started: list[int] = []
    for command in commands:
        if not control(login_id, channel, command, 0, speed, 0, 0, None):
            error = _last_error(library)
            for done in reversed(started):
                control(login_id, channel, done, 0, speed, 0, 1, None)
            raise WorkerError(f"PTZ Dahua recusado (codigo {error}).")
        started.append(command)
    return started


def _ptz_stop(library: ctypes.CDLL, login_id: int, payload: dict[str, Any]) -> None:
    channel = max(0, int(payload.get("channel", 1)) - 1)
    speed = max(1, min(7, int(payload.get("speed", 4))))
    commands = [int(item) for item in (payload.get("commands") or [])]
    if not commands:
        return
    control = _ptz_control_func(library)
    for command in reversed(commands):
        control(login_id, channel, command, 0, speed, 0, 1, None)


def _ptz_3d(library: ctypes.CDLL, login_id: int, payload: dict[str, Any]) -> None:
    """Posicionamento 3D Dahua/Intelbras via coordenadas normalizadas 0..255."""

    channel = max(0, int(payload.get("channel", 1)) - 1)
    coordinates = [
        max(0, min(255, int(payload.get(name, 0))))
        for name in ("x_start", "y_start", "x_end", "y_end")
    ]
    x_start, y_start, x_end, y_end = coordinates
    center_x = round((((x_start + x_end) / 2) / 255 * 2 - 1) * 8191)
    center_y = round((((y_start + y_end) / 2) / 255 * 2 - 1) * 8191)

    horizontal_span = abs(x_end - x_start)
    vertical_span = abs(y_end - y_start)
    span = max(horizontal_span, vertical_span)
    if span <= 2:
        zoom = 0
    else:
        # O FASTGOTO aceita passo de zoom entre -16 e 16. Uma seleção menor
        # pede uma aproximação maior; a direção horizontal define in/out.
        magnitude = max(1, min(16, round(16 * (1 - span / 255))))
        zoom = magnitude if x_end > x_start else -magnitude

    control = _ptz_control_func(library)
    if not control(
        login_id,
        channel,
        DH_EXTPTZ_FASTGOTO,
        center_x,
        center_y,
        zoom,
        0,
        None,
    ):
        raise WorkerError(
            f"Posicionamento 3D Dahua recusado (codigo {_last_error(library)})."
        )


def _goto_preset(library: ctypes.CDLL, login_id: int, payload: dict[str, Any]) -> None:
    channel = max(0, int(payload.get("channel", 1)) - 1)
    try:
        preset = int(payload.get("preset_token"))
    except (TypeError, ValueError) as exc:
        raise WorkerError("Numero do preset Dahua invalido.") from exc
    if not 1 <= preset <= 300:
        raise WorkerError("O preset Dahua deve estar entre 1 e 300.")
    # DH_PTZ_POINT_MOVE_CONTROL = 10. Apenas chama uma posicao existente.
    control = _ptz_control_func(library)
    if not control(login_id, channel, 10, 0, preset, 0, 0, None):
        raise WorkerError(
            f"Acionamento do preset Dahua recusado (codigo {_last_error(library)})."
        )


def _decode_preset_name(raw: bytes, index: int) -> str:
    value = raw.split(b"\0", 1)[0]
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            text = value.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
        if text:
            return text
    return f"Preset {index}"


def _list_presets(
    library: ctypes.CDLL,
    login_id: int,
    payload: dict[str, Any],
) -> list[dict[str, str]]:
    channel = max(0, int(payload.get("channel", 1)) - 1)
    query = getattr(library, "CLIENT_QueryRemotDevState", None)
    if query is None:
        raise WorkerError("Esta versao do Dahua NetSDK nao lista presets por canal.")

    records = ctypes.create_string_buffer(
        PTZ_PRESET_MAX_COUNT * PTZ_PRESET_RECORD_SIZE
    )
    preset_list = NET_PTZ_PRESET_LIST(
        dwSize=ctypes.sizeof(NET_PTZ_PRESET_LIST),
        dwMaxPresetNum=PTZ_PRESET_MAX_COUNT,
        dwRetPresetNum=PTZ_PRESET_MAX_COUNT,
        pstuPtzPorsetList=ctypes.addressof(records),
    )
    returned = ctypes.c_int32()
    query.argtypes = [
        LLONG,
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int32),
        ctypes.c_int32,
    ]
    query.restype = BOOL
    if not query(
        login_id,
        DH_DEVSTATE_PTZ_PRESET_LIST,
        channel,
        ctypes.byref(preset_list),
        ctypes.sizeof(preset_list),
        ctypes.byref(returned),
        5000,
    ):
        raise WorkerError(
            f"Listagem de presets Dahua recusada "
            f"(codigo {_last_error(library)}, canal {channel + 1})."
        )

    count = min(PTZ_PRESET_MAX_COUNT, int(preset_list.dwRetPresetNum))
    raw_records = records.raw
    presets: list[dict[str, str]] = []
    seen: set[int] = set()
    for position in range(count):
        offset = position * PTZ_PRESET_RECORD_SIZE
        index = int.from_bytes(
            raw_records[offset : offset + 4],
            byteorder=sys.byteorder,
            signed=True,
        )
        if not 1 <= index <= PTZ_PRESET_MAX_COUNT or index in seen:
            continue
        seen.add(index)
        name = _decode_preset_name(raw_records[offset + 4 : offset + 68], index)
        presets.append({"token": str(index), "name": name})
    return sorted(presets, key=lambda item: int(item["token"]))


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
    login_id: int,
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
                    _ptz_stop(library, login_id, {**current_payload, "commands": current_commands})
                    pan, tilt, zoom, speed = vector
                    current_payload = {**payload, "pan": pan, "tilt": tilt, "zoom": zoom, "speed": speed}
                    current_vector = vector
                    try:
                        current_commands = _ptz_start(library, login_id, current_payload)
                    except WorkerError:
                        # Direcao recusada pelo equipamento: fica parado, mas
                        # mantem a sessao viva para a proxima atualizacao/stop.
                        current_commands = []
            time.sleep(0.05)
    finally:
        _ptz_stop(library, login_id, {**current_payload, "commands": current_commands})


def run(action: str, payload: dict[str, Any], result_path: Path) -> dict[str, object] | None:
    """Retorna o dict de resultado para o chamador (main) gravar, ou None se
    esta funcao ja gravou o resultado ela mesma (caso do ptz_start, que
    precisa publicar o resultado antes de continuar rodando)."""
    if action not in {
        "inspect", "snapshot", "ptz", "ptz_start", "ptz_stop", "ptz_3d",
        "goto_preset", "list_presets",
    }:
        raise WorkerError("Acao SDK invalida.")
    library = _load_library()
    login_id = 0
    initialized = False
    callback_ref: object | None = None
    try:
        login_id, raw_info, callback_ref = _login(library, payload)
        initialized = True
        if action == "inspect":
            return {
                "ok": True,
                "device": _inspect(
                    library,
                    login_id,
                    raw_info,
                    int(payload.get("channel", 1)),
                ),
            }
        if action == "snapshot":
            _snapshot(library, login_id, payload)
        elif action == "ptz":
            _ptz(library, login_id, payload)
        elif action == "ptz_start":
            commands = _ptz_start(library, login_id, payload)
            _write_result(result_path, {"ok": True, "commands": commands})
            max_hold = max(5.0, min(120.0, float(payload.get("max_hold_seconds", 90))))
            _hold_ptz(library, login_id, payload, commands, result_path, max_hold)
            return None
        elif action == "ptz_3d":
            _ptz_3d(library, login_id, payload)
        elif action == "goto_preset":
            _goto_preset(library, login_id, payload)
        elif action == "list_presets":
            return {"ok": True, "presets": _list_presets(library, login_id, payload)}
        else:
            _ptz_stop(library, login_id, payload)
        return {"ok": True}
    finally:
        _ = callback_ref
        if login_id:
            library.CLIENT_Logout.argtypes = [LLONG]
            library.CLIENT_Logout(login_id)
        if initialized:
            library.CLIENT_Cleanup.argtypes = []
            library.CLIENT_Cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("action")
    parser.add_argument("--result-file", required=True)
    args = parser.parse_args()
    result_path = Path(args.result_file)
    try:
        result = run(args.action, _payload(), result_path)
    except Exception as exc:
        result = {"ok": False, "error": str(exc) if isinstance(exc, WorkerError) else "Falha nativa no Dahua NetSDK."}
    if result is None:
        return 0
    _write_result(result_path, result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
