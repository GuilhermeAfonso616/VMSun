"""Sessões temporárias e execução isolada dos SDKs Hikvision e Dahua, e da API HTTP Intelbras."""

from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from requests import exceptions as requests_exceptions

from app.services.webrtc_gateway_client import (
    build_webrtc_player_url,
    cleanup_webrtc_paths,
    register_webrtc_path,
    unregister_webrtc_path,
)
from app.services.sdk_package_service import installed_library_path
from app.video_sources.providers.dahua_http_api import DahuaHttpApiClient, DahuaHttpApiError
from app.camera.onvif_client import _rewrite_xaddrs, get_wsdl_dir, onvif_transport


class HikSdkLabError(RuntimeError):
    """Erro seguro para apresentação na interface do laboratório."""


@dataclass(slots=True)
class HikSdkLabSession:
    token: str
    owner_id: int
    label: str
    device_type: str
    host: str
    port: int
    username: str
    password: str
    channel: int
    created_at: float
    last_used_at: float
    device: dict[str, Any]
    manufacturer: str = "hikvision"
    rtsp_port: int = 554
    stream_kind: str = "sub"
    stream_path: str | None = None
    stream_width: int = 0
    stream_height: int = 0
    connection_mode: str = "sdk"
    active_ptz_code: str | None = None
    active_ptz_process: subprocess.Popen | None = None
    active_ptz_result_path: Path | None = None


_sessions: dict[str, HikSdkLabSession] = {}
_sessions_lock = threading.Lock()
_orphan_cleanup_done = False


def _ttl_seconds() -> int:
    try:
        return max(60, min(3600, int(os.getenv("HIK_SDK_SESSION_TTL_SECONDS", "900"))))
    except ValueError:
        return 900


def _enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def sdk_available(manufacturer: str | None = None) -> bool:
    if manufacturer is None:
        return sdk_available("hikvision") or sdk_available("dahua") or sdk_available("intelbras")
    if manufacturer == "intelbras":
        # API HTTP CGI: sem biblioteca nativa para montar, sempre disponivel.
        return True
    installed = installed_library_path(manufacturer)
    if installed.is_file():
        return True
    if manufacturer == "hikvision":
        return _enabled("HIK_SDK_ENABLED") and (
            Path(os.getenv("HIK_SDK_LIB_DIR", "/opt/hikvision/lib")) / "libhcnetsdk.so"
        ).is_file()
    if manufacturer == "dahua":
        return _enabled("DAHUA_SDK_ENABLED") and (
            Path(os.getenv("DAHUA_SDK_LIB_DIR", "/opt/dahua/lib")) / "libdhnetsdk.so"
        ).is_file()
    return False


def sdk_availability() -> dict[str, bool]:
    return {name: sdk_available(name) for name in ("hikvision", "dahua", "intelbras")}


def public_ips_allowed() -> bool:
    return os.getenv("HIK_SDK_ALLOW_PUBLIC_IPS", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def validate_host(value: str) -> str:
    host = str(value or "").strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise HikSdkLabError("Informe um endereço IPv4 ou IPv6 válido do equipamento.") from exc
    if address.is_loopback or address.is_multicast or address.is_unspecified:
        raise HikSdkLabError("Esse endereço não pode ser usado no laboratório SDK.")
    if not (address.is_private or address.is_link_local) and not public_ips_allowed():
        raise HikSdkLabError(
            "Endereços públicos estão desabilitados. Ative HIK_SDK_ALLOW_PUBLIC_IPS para liberar."
        )
    return host


def _stop_signal_path(result_path: Path) -> Path:
    return result_path.with_suffix(result_path.suffix + ".stop")


def _command_signal_path(result_path: Path) -> Path:
    return result_path.with_suffix(result_path.suffix + ".cmd")


def _write_command_update(result_path: Path, *, pan: int, tilt: int, zoom: int, speed: int) -> None:
    """Publica a nova direcao/velocidade do joystick para o worker de hold ler
    e reaplicar na MESMA sessao de login. Escrita atomica (tmp + rename) para o
    worker nunca ler um arquivo parcial. Cada escrita tambem renova o teto de
    seguranca do worker, funcionando como keepalive enquanto o gesto continua."""
    command_path = _command_signal_path(result_path)
    tmp_path = command_path.with_suffix(command_path.suffix + ".tmp")
    payload = json.dumps({"pan": int(pan), "tilt": int(tilt), "zoom": int(zoom), "speed": int(speed)})
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, command_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)


def _terminate_session_ptz(session: HikSdkLabSession) -> None:
    """Cria o arquivo de sinal de stop que o worker esta esperando (ele
    mesmo executa o stop no equipamento e desloga antes de sair) e limpa os
    arquivos temporarios. Bloqueia ate 5s esperando o processo sair."""
    process = session.active_ptz_process
    result_path = session.active_ptz_result_path
    session.active_ptz_process = None
    session.active_ptz_result_path = None
    if process is None:
        return
    stop_path = _stop_signal_path(result_path) if result_path is not None else None
    try:
        if stop_path is not None:
            try:
                stop_path.write_text("stop", encoding="utf-8")
            except OSError:
                pass
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)
    finally:
        if result_path is not None:
            result_path.unlink(missing_ok=True)
            Path(str(result_path) + ".tmp").unlink(missing_ok=True)
            command_path = _command_signal_path(result_path)
            command_path.unlink(missing_ok=True)
            Path(str(command_path) + ".tmp").unlink(missing_ok=True)
        if stop_path is not None:
            stop_path.unlink(missing_ok=True)


def _terminate_session_ptz_async(session: HikSdkLabSession) -> None:
    if session.active_ptz_process is None:
        return
    threading.Thread(target=_terminate_session_ptz, args=(session,), daemon=True).start()


def _purge_expired(now: float | None = None) -> None:
    current = time.time() if now is None else now
    ttl = _ttl_seconds()
    expired = [token for token, session in _sessions.items() if current - session.last_used_at > ttl]
    for token in expired:
        session = _sessions.pop(token, None)
        if session:
            _terminate_session_ptz_async(session)
            if session.stream_path:
                unregister_webrtc_path(session.stream_path)


_DAHUA_PROTOCOL_MANUFACTURERS = {"dahua", "intelbras"}


def _worker_path(manufacturer: str) -> Path:
    filename = "dahua_sdk_worker.py" if manufacturer in _DAHUA_PROTOCOL_MANUFACTURERS else "hik_sdk_worker.py"
    return Path(__file__).resolve().parents[2] / "scripts" / filename


def _worker_environment(manufacturer: str) -> dict[str, str]:
    environment = os.environ.copy()
    installed = installed_library_path(manufacturer)
    if installed.is_file():
        variable = "DAHUA_SDK_LIB_DIR" if manufacturer in _DAHUA_PROTOCOL_MANUFACTURERS else "HIK_SDK_LIB_DIR"
        environment[variable] = str(installed.parent)
    return environment


def intelbras_native_ready() -> bool:
    """Intelbras compartilha o protocolo NetSDK da Dahua (mesmo OEM).

    Um pacote proprio enviado para o fabricante "intelbras" tem prioridade;
    na ausencia dele, reaproveita a biblioteca Dahua ja instalada/habilitada.
    """
    if installed_library_path("intelbras").is_file():
        return True
    return sdk_available("dahua")


def _run_worker(action: str, payload: dict[str, Any], *, manufacturer: str = "hikvision",
                timeout: float = 15.0) -> dict[str, Any]:
    if not sdk_available(manufacturer):
        label = "Dahua NetSDK" if manufacturer == "dahua" else "HCNetSDK"
        raise HikSdkLabError(
            f"{label} indisponível. Habilite e monte a biblioteca Linux no container web."
        )
    result_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f"{manufacturer}-sdk-result-", suffix=".json", delete=False) as handle:
            result_path = Path(handle.name)
        worker_path = _worker_path(manufacturer)
        if not worker_path.is_file():
            raise HikSdkLabError("Worker auditado do SDK nao esta instalado na aplicacao.")
        command = [sys.executable, str(worker_path), action, "--result-file", str(result_path)]
        completed = subprocess.run(
            command,
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=_worker_environment(manufacturer),
        )
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            detail = completed.stderr.decode("utf-8", "replace")[-500:].strip()
            raise HikSdkLabError(f"O processo do SDK não retornou um resultado válido. {detail}".strip()) from exc
        if not result.get("ok"):
            raise HikSdkLabError(str(result.get("error") or "Operação recusada pelo equipamento."))
        return result
    except subprocess.TimeoutExpired as exc:
        raise HikSdkLabError("Tempo limite excedido ao comunicar com o equipamento.") from exc
    finally:
        if result_path is not None:
            result_path.unlink(missing_ok=True)


def _intelbras_client(*, host: str, port: int, username: str, password: str) -> DahuaHttpApiClient:
    return DahuaHttpApiClient(
        host=host,
        port=int(port),
        username=username,
        password=password,
        preset_channel_one_based=True,
    )


def _intelbras_inspect(
    *, host: str, port: int, username: str, password: str, channel: int
) -> dict[str, Any]:
    try:
        client = _intelbras_client(
            host=host, port=port, username=username, password=password
        )
        serial_number = client.get_serial_number()
        # ``getPresets`` e uma consulta sem movimento, mas e processada pelo
        # modulo PTZ do canal. Uma resposta valida (inclusive lista vazia)
        # confirma que o CGI PTZ esta disponivel para aquele canal.
        presets = client.get_presets(channel=channel)
    except (DahuaHttpApiError, requests_exceptions.RequestException) as exc:
        raise HikSdkLabError(f"Falha ao conectar via API HTTP Intelbras: {exc}") from exc
    return {
        "device": {
            "serial_number": serial_number,
            "ptz_capability_verified": True,
            "ptz_capable": True,
            "ptz_pan": True,
            "ptz_zoom": True,
            "ptz_preset_count": len(presets),
            "ptz_capability_reason": "CGI PTZ do canal confirmado por consulta de presets.",
        }
    }


def _credentials(session: HikSdkLabSession) -> dict[str, Any]:
    return {
        "host": session.host,
        "port": session.port,
        "username": session.username,
        "password": session.password,
        "channel": int(session.device.get("sdk_channel") or session.channel),
    }


def connect_device(*, owner_id: int, label: str, device_type: str, host: str, port: int,
                   username: str, password: str, channel: int,
                   manufacturer: str = "hikvision", rtsp_port: int = 554,
                   stream_kind: str = "sub", connection_mode: str = "sdk") -> dict[str, Any]:
    host = validate_host(host)
    if manufacturer not in {"hikvision", "dahua", "intelbras"}:
        raise HikSdkLabError("Fabricante inválido.")
    if device_type not in {"nvr", "camera"}:
        raise HikSdkLabError("Tipo de equipamento inválido.")
    if not 1 <= int(port) <= 65535:
        raise HikSdkLabError("A porta deve estar entre 1 e 65535.")
    if not username or not password:
        raise HikSdkLabError("Informe usuário e senha do equipamento.")
    if not 1 <= int(channel) <= 4096:
        raise HikSdkLabError("O canal deve estar entre 1 e 4096.")
    if not 1 <= int(rtsp_port) <= 65535:
        raise HikSdkLabError("A porta RTSP deve estar entre 1 e 65535.")
    if stream_kind not in {"main", "sub"}:
        raise HikSdkLabError("Tipo de stream inválido.")

    if connection_mode == "onvif":
        try:
            from onvif import ONVIFCamera
            onvif_cam = ONVIFCamera(
                host,
                int(port),
                username,
                password,
                wsdl_dir=get_wsdl_dir(),
                transport=onvif_transport(),
            )
            _rewrite_xaddrs(onvif_cam, host, int(port))
            devicemgmt = onvif_cam.devicemgmt
            raw_info = devicemgmt.GetDeviceInformation()
            result = {
                "device": {
                    "manufacturer": _text(getattr(raw_info, "Manufacturer", None)) or manufacturer,
                    "model": _text(getattr(raw_info, "Model", None)) or "ONVIF Device",
                    "firmware_version": _text(getattr(raw_info, "FirmwareVersion", None)) or "—",
                    "serial_number": _text(getattr(raw_info, "SerialNumber", None)) or "—",
                    "sdk_build": "ONVIF",
                }
            }
        except Exception as exc:
            msg = str(exc)
            if "Sender not Authorized" in msg or "Invalid username or password" in msg:
                raise HikSdkLabError(
                    f"Credenciais ONVIF recusadas pela câmera ({host}:{port}). "
                    "Verifique se o usuário ONVIF está ativado em 'Integração / Protocolos' na câmera."
                ) from exc
            raise HikSdkLabError(f"Falha na conexão ONVIF em {host}:{port}: {exc}") from exc
    else:
        if manufacturer == "intelbras":
            result = _intelbras_inspect(
                host=host,
                port=int(port),
                username=username,
                password=password,
                channel=int(channel),
            )
        else:
            result = _run_worker("inspect", {
                "host": host, "port": int(port), "username": username,
                "password": password, "channel": int(channel),
            }, manufacturer=manufacturer)
    now = time.time()
    session = HikSdkLabSession(
        token=uuid.uuid4().hex,
        owner_id=int(owner_id),
        label=(str(label or "").strip() or f"{device_type.upper()} {host}")[:80],
        device_type=device_type,
        host=host,
        port=int(port),
        username=username,
        password=password,
        channel=int(channel),
        created_at=now,
        last_used_at=now,
        device=dict(result.get("device") or {}),
        manufacturer=manufacturer,
        rtsp_port=int(rtsp_port),
        stream_kind=stream_kind,
        connection_mode=connection_mode,
    )
    with _sessions_lock:
        _purge_expired(now)
        _sessions[session.token] = session
    return public_session(session)


def public_session(session: HikSdkLabSession) -> dict[str, Any]:
    return {
        "token": session.token,
        "label": session.label,
        "device_type": session.device_type,
        "manufacturer": session.manufacturer,
        "host": session.host,
        "port": session.port,
        "channel": session.channel,
        "rtsp_port": session.rtsp_port,
        "stream_kind": session.stream_kind,
        "stream_active": bool(session.stream_path),
        "created_at": session.created_at,
        "expires_in_seconds": max(0, _ttl_seconds() - int(time.time() - session.last_used_at)),
        "device": session.device,
        "connection_mode": session.connection_mode,
    }


def get_session(token: str, owner_id: int) -> HikSdkLabSession:
    with _sessions_lock:
        _purge_expired()
        session = _sessions.get(token)
        if session is None or session.owner_id != int(owner_id):
            raise HikSdkLabError("Sessão SDK inexistente ou expirada.")
        session.last_used_at = time.time()
        return session


def list_sessions(owner_id: int) -> list[dict[str, Any]]:
    with _sessions_lock:
        _purge_expired()
        return [public_session(item) for item in _sessions.values() if item.owner_id == int(owner_id)]


def disconnect(token: str, owner_id: int) -> None:
    with _sessions_lock:
        session = _sessions.get(token)
        if session is None or session.owner_id != int(owner_id):
            raise HikSdkLabError("Sessão SDK inexistente ou expirada.")
        _sessions.pop(token, None)
    _terminate_session_ptz(session)
    if session.stream_path:
        unregister_webrtc_path(session.stream_path)


def cleanup_orphaned_streams() -> None:
    global _orphan_cleanup_done
    with _sessions_lock:
        if _orphan_cleanup_done:
            return
        _orphan_cleanup_done = True
    cleanup_webrtc_paths("sdk_lab_")


def _rtsp_url(session: HikSdkLabSession, *, stream_kind: str | None = None) -> str:
    username = quote(session.username, safe="")
    password = quote(session.password, safe="")
    host = f"[{session.host}]" if ":" in session.host else session.host
    authority = f"{username}:{password}@{host}:{session.rtsp_port}"
    selected_stream_kind = stream_kind if stream_kind in {"main", "sub"} else session.stream_kind
    subtype = 0 if selected_stream_kind == "main" else 1
    if session.manufacturer in {"dahua", "intelbras"}:
        return f"rtsp://{authority}/cam/realmonitor?channel={session.channel}&subtype={subtype}"
    stream_suffix = "01" if selected_stream_kind == "main" else "02"
    return f"rtsp://{authority}/Streaming/Channels/{session.channel}{stream_suffix}"


def start_stream(token: str, owner_id: int) -> dict[str, Any]:
    session = get_session(token, owner_id)
    path_name = session.stream_path or f"sdk_lab_{session.token[:24]}"
    result = register_webrtc_path(path_name, _rtsp_url(session))
    if not result.get("ok"):
        raise HikSdkLabError("Não foi possível registrar o vídeo temporário no gateway WebRTC.")
    session.stream_path = path_name
    return {
        "path": path_name,
        "player_url": build_webrtc_player_url(path_name),
        "stream_kind": session.stream_kind,
    }


def capture_snapshot(token: str, owner_id: int) -> bytes:
    session = get_session(token, owner_id)
    if session.manufacturer == "intelbras" and not intelbras_native_ready():
        try:
            return _intelbras_client(
                host=session.host, port=session.port,
                username=session.username, password=session.password,
            ).get_snapshot(channel=session.channel)
        except (DahuaHttpApiError, requests_exceptions.RequestException) as exc:
            raise HikSdkLabError(f"Falha ao capturar imagem via API HTTP Intelbras: {exc}") from exc
    with tempfile.NamedTemporaryFile(prefix="hik-sdk-frame-", suffix=".jpg", delete=False) as handle:
        frame_path = Path(handle.name)
    try:
        payload = {**_credentials(session), "output_file": str(frame_path)}
        _run_worker("snapshot", payload, manufacturer=session.manufacturer, timeout=20.0)
        frame = frame_path.read_bytes()
        if not frame.startswith(b"\xff\xd8"):
            raise HikSdkLabError("O equipamento não retornou uma imagem JPEG válida.")
        return frame
    finally:
        frame_path.unlink(missing_ok=True)


def _ffprobe_binary() -> str:
    return os.getenv("FFPROBE_BINARY", "ffprobe")


# Codecs de áudio que o WebRTC do navegador reproduz sem transcodificação.
_WEBRTC_AUDIO_CODECS = {"pcm_mulaw", "pcm_alaw", "opus", "g722"}
# AAC é comum em câmeras, mas o WebRTC não o aceita direto (precisa Opus).
_TRANSCODE_AUDIO_CODECS = {"aac"}


def probe_audio(token: str, owner_id: int) -> dict[str, Any]:
    """Analisa via ffprobe a track de áudio do mesmo RTSP que alimenta o WebRTC,
    classificando a compatibilidade com o player do navegador.

    O áudio ao vivo é reproduzido pelo próprio player WebRTC (basta tirar o
    mute); esta análise só informa o operador se o codec toca direto ou se a
    câmera está entregando algo (ex.: AAC) que exigiria transcodificação.
    """
    session = get_session(token, owner_id)
    command = [
        _ffprobe_binary(), "-v", "error",
        "-rtsp_transport", "tcp",
        "-select_streams", "a",
        "-show_entries", "stream=codec_name,codec_long_name,sample_rate,channels",
        "-of", "json",
        _rtsp_url(session),
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20.0,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HikSdkLabError("Tempo limite ao analisar o áudio do fluxo RTSP.") from exc
    except FileNotFoundError as exc:
        raise HikSdkLabError("ffprobe não está disponível no container web.") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace")[-300:].strip()
        raise HikSdkLabError(f"Falha ao analisar o fluxo RTSP. {detail}".strip())
    try:
        parsed = json.loads(completed.stdout.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HikSdkLabError("O ffprobe não retornou um resultado válido.") from exc

    streams = [item for item in (parsed.get("streams") or []) if isinstance(item, dict)]
    if not streams:
        return {"has_audio": False, "compatibility": "none", "stream_kind": session.stream_kind}

    raw = streams[0]
    codec = str(raw.get("codec_name") or "").lower()
    if codec in _WEBRTC_AUDIO_CODECS:
        compatibility = "ok"
    elif codec in _TRANSCODE_AUDIO_CODECS:
        compatibility = "transcode"
    else:
        compatibility = "unknown"
    return {
        "has_audio": True,
        "compatibility": compatibility,
        "codec": raw.get("codec_name"),
        "codec_long_name": raw.get("codec_long_name"),
        "sample_rate": raw.get("sample_rate"),
        "channels": raw.get("channels"),
        "stream_kind": session.stream_kind,
    }


def _stream_dimensions_from_rtsp(session: HikSdkLabSession, stream_kind: str) -> tuple[int, int]:
    command = [
        _ffprobe_binary(), "-v", "error",
        "-rtsp_transport", "tcp",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,sample_aspect_ratio,display_aspect_ratio",
        "-of", "json",
        _rtsp_url(session, stream_kind=stream_kind),
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20.0,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HikSdkLabError("Tempo limite ao medir as dimensoes do fluxo RTSP.") from exc
    except FileNotFoundError as exc:
        raise HikSdkLabError("ffprobe nao esta disponivel no container web.") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace")[-300:].strip()
        raise HikSdkLabError(f"Falha ao medir o fluxo RTSP. {detail}".strip())
    try:
        parsed = json.loads(completed.stdout.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HikSdkLabError("O ffprobe nao retornou um resultado valido.") from exc
    for raw in parsed.get("streams") or []:
        if not isinstance(raw, dict):
            continue
        try:
            width = int(raw.get("width") or 0)
            height = int(raw.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if width > 0 and height > 0:
            display_ratio = str(raw.get("display_aspect_ratio") or "").strip()
            if ":" in display_ratio:
                try:
                    ratio_width, ratio_height = (int(part) for part in display_ratio.split(":", 1))
                    if ratio_width > 0 and ratio_height > 0:
                        return max(1, round(height * ratio_width / ratio_height)), height
                except (TypeError, ValueError):
                    pass
            sample_ratio = str(raw.get("sample_aspect_ratio") or "").strip()
            if ":" in sample_ratio:
                try:
                    sample_width, sample_height = (int(part) for part in sample_ratio.split(":", 1))
                    if sample_width > 0 and sample_height > 0:
                        return max(1, round(width * sample_width / sample_height)), height
                except (TypeError, ValueError):
                    pass
            return width, height
    raise HikSdkLabError("Nao foi possivel medir as dimensoes do fluxo.")


def stream_dimensions(token: str, owner_id: int) -> tuple[int, int]:
    """Retorna a geometria de referencia do PTZ 3D.

    O stream principal representa a proporcao da camera usada pelo comando
    nativo. O player pode continuar no substream para economizar banda. Quando
    o principal nao responde, usa o fluxo exibido como contingencia. O DAR do
    codec tem prioridade sobre largura/altura codificadas, pois substreams
    Dahua frequentemente usam pixels nao quadrados."""
    session = get_session(token, owner_id)
    if session.stream_width > 0 and session.stream_height > 0:
        return session.stream_width, session.stream_height

    stream_candidates = ["main"]
    if session.stream_kind != "main":
        stream_candidates.append(session.stream_kind)
    last_error: HikSdkLabError | None = None
    for stream_kind in stream_candidates:
        try:
            width, height = _stream_dimensions_from_rtsp(session, stream_kind)
            session.stream_width = width
            session.stream_height = height
            return width, height
        except HikSdkLabError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise HikSdkLabError("Nao foi possivel medir as dimensoes do fluxo.")


def move_ptz(token: str, owner_id: int, *, pan: int, tilt: int, zoom: int,
             speed: int, duration_ms: int) -> None:
    session = get_session(token, owner_id)
    bounded = {
        "pan": max(-1, min(1, int(pan))),
        "tilt": max(-1, min(1, int(tilt))),
        "zoom": max(-1, min(1, int(zoom))),
        "speed": max(1, min(7, int(speed))),
        "duration_ms": max(80, min(800, int(duration_ms))),
    }
    if session.connection_mode == "onvif":
        try:
            ptz, profile_token = _onvif_ptz_for_session(session)
            velocity = bounded["speed"] / 7.0
            from datetime import timedelta
            ptz.ContinuousMove({
                "ProfileToken": profile_token,
                "Velocity": {
                    "PanTilt": {
                        "x": bounded["pan"] * velocity,
                        "y": bounded["tilt"] * velocity,
                    },
                    "Zoom": {
                        "x": bounded["zoom"] * velocity,
                    },
                },
                "Timeout": timedelta(milliseconds=bounded["duration_ms"]),
            })
        except Exception as exc:
            raise HikSdkLabError(f"Falha ao mover PTZ via ONVIF: {exc}") from exc
        return

    if session.manufacturer == "intelbras" and not intelbras_native_ready():
        try:
            _intelbras_client(
                host=session.host, port=session.port,
                username=session.username, password=session.password,
            ).ptz_move(channel=session.channel, **bounded)
        except (DahuaHttpApiError, requests_exceptions.RequestException) as exc:
            raise HikSdkLabError(f"Falha ao mover PTZ via API HTTP Intelbras: {exc}") from exc
        return
    _run_worker("ptz", {**_credentials(session), **bounded}, manufacturer=session.manufacturer, timeout=5.0)


def position_ptz_3d(
    token: str,
    owner_id: int,
    *,
    x_start: int,
    y_start: int,
    x_end: int,
    y_end: int,
) -> None:
    """Centraliza ou enquadra uma região usando o posicionamento 3D nativo."""

    session = get_session(token, owner_id)
    if session.connection_mode != "sdk":
        raise HikSdkLabError("O posicionamento 3D requer o SDK do fabricante.")
    if session.manufacturer == "intelbras" and not intelbras_native_ready():
        raise HikSdkLabError(
            "O posicionamento 3D Intelbras requer o NetSDK nativo instalado."
        )
    coordinates = {
        name: max(0, min(255, int(value)))
        for name, value in {
            "x_start": x_start,
            "y_start": y_start,
            "x_end": x_end,
            "y_end": y_end,
        }.items()
    }
    credentials = _credentials(session)
    if session.manufacturer == "intelbras":
        # A sessão Intelbras é aberta pela API HTTP (porta 80), mas o comando
        # 3D é do NetSDK OEM e usa a porta de serviço nativa.
        credentials["port"] = int(session.device.get("sdk_port") or 37777)
    _terminate_session_ptz(session)
    _run_worker(
        "ptz_3d",
        {**credentials, **coordinates},
        manufacturer=session.manufacturer,
        timeout=10.0,
    )


# move_ptz acima sempre faz um pulso limitado (start + sleep + stop, ou um
# ContinuousMove com Timeout curto) porque e o unico jeito de mover no worker
# nativo (processo por comando) e no ONVIF com Timeout ligado a duration_ms.
#
# ptz_hold_start/ptz_hold_stop tentam um hold de verdade nos workers nativos
# Hikvision/Dahua (via NET_DVR_PTZControlWithSpeed_Other / CLIENT_DHPTZControlEx2).
# O worker manda o comando de "comecar a mover" e FICA RODANDO, preso esperando
# um sinal no stdin, antes de deslogar do equipamento -- varias cameras param o
# motor sozinhas assim que a sessao do NetSDK que emitiu o comando desconecta,
# entao um processo que loga, manda o start e desloga na hora (como era antes)
# fazia a camera andar uma "puxadinha" e parar sozinha, mesmo com o software
# achando que o movimento continuava. ptz_hold_stop manda o sinal de stop pro
# mesmo processo, que so ai executa o stop de verdade e desloga.
def ptz_hold_start(token: str, owner_id: int, *, pan: int, tilt: int, zoom: int, speed: int) -> bool:
    """Inicia movimento continuo real quando o backend permite.

    Retorna True se o movimento continuo foi iniciado (o chamador deve usar
    ptz_hold_stop para parar); False se este backend/sessao nao suporta ou a
    tentativa falhou, e o chamador deve cair no fallback por pulso (move_ptz
    repetido).
    """
    session = get_session(token, owner_id)
    if session.connection_mode == "onvif":
        return False

    bounded_pan = max(-1, min(1, int(pan)))
    bounded_tilt = max(-1, min(1, int(tilt)))
    bounded_zoom = max(-1, min(1, int(zoom)))
    bounded_speed = max(1, min(7, int(speed)))

    if session.manufacturer == "intelbras":
        # session.port e garantidamente a porta HTTP do CGI por convencao do
        # formulario de conexao (diferente de sessoes "dahua", que guardam a
        # porta do NetSDK nativo 37777 -- por isso essas usam o worker abaixo).
        try:
            code = _intelbras_client(
                host=session.host, port=session.port,
                username=session.username, password=session.password,
            ).ptz_start(
                channel=session.channel,
                pan=bounded_pan, tilt=bounded_tilt, zoom=bounded_zoom, speed=bounded_speed,
            )
        except (DahuaHttpApiError, requests_exceptions.RequestException):
            return False
        session.active_ptz_code = code
        return True

    if session.active_ptz_process is not None:
        if session.active_ptz_process.poll() is None:
            # Ja existe um hold vivo nesta sessao (ex.: arraste do joystick
            # reenviando sem soltar). Nao abre processo duplicado: publica a
            # nova direcao/velocidade para o worker reaplicar na mesma sessao.
            # Antes isso retornava cedo e descartava a mudanca, deixando a
            # camera presa na direcao/velocidade inicial do gesto (so as setas,
            # que soltam entre toques, funcionavam).
            if session.active_ptz_result_path is not None:
                _write_command_update(
                    session.active_ptz_result_path,
                    pan=bounded_pan, tilt=bounded_tilt, zoom=bounded_zoom, speed=bounded_speed,
                )
            return True
        # O hold anterior ja terminou (teto de seguranca ou crash do worker):
        # limpa os vestigios e cai para iniciar um novo hold abaixo.
        _terminate_session_ptz(session)

    if not sdk_available(session.manufacturer):
        return False
    worker_path = _worker_path(session.manufacturer)
    if not worker_path.is_file():
        return False

    payload = {
        **_credentials(session),
        "pan": bounded_pan, "tilt": bounded_tilt, "zoom": bounded_zoom, "speed": bounded_speed,
    }
    with tempfile.NamedTemporaryFile(prefix=f"{session.manufacturer}-ptz-hold-", suffix=".json", delete=False) as handle:
        result_path = Path(handle.name)
    result_path.unlink(missing_ok=True)

    try:
        process = subprocess.Popen(
            [sys.executable, str(worker_path), "ptz_start", "--result-file", str(result_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=_worker_environment(session.manufacturer),
        )
    except OSError:
        result_path.unlink(missing_ok=True)
        return False

    # Drena o stderr numa thread desde ja: se ninguem ler esse pipe, um filho
    # que escreva mais que o buffer do SO (ex.: log nativo verboso da SDK)
    # trava esperando alguem consumir, e o polling abaixo fica parado pra
    # sempre esperando um resultado que nunca vem.
    _drain_stderr_async(process)

    try:
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload).encode("utf-8"))
        process.stdin.flush()
        # Fecha (EOF) logo depois de escrever: o _payload() do worker faz um
        # read bufferizado que so retorna com N bytes ou EOF, entao deixar o
        # pipe aberto aqui travaria essa leitura inicial pra sempre. O sinal
        # de stop mais tarde usa um arquivo (_stop_signal_path), nao o stdin.
        process.stdin.close()
    except (OSError, ValueError):
        _kill_ptz_process(process)
        result_path.unlink(missing_ok=True)
        return False

    result: dict[str, Any] | None = None
    # Um login nativo saudável responde em menos de um segundo. Não segure a
    # fila de comandos/HTTP por 10 s quando o equipamento está indisponível.
    deadline = time.time() + 4.0
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            text = result_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        if text:
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                result = None
            break
        time.sleep(0.05)

    if not result or not result.get("ok"):
        _kill_ptz_process(process)
        result_path.unlink(missing_ok=True)
        return False

    session.active_ptz_process = process
    session.active_ptz_result_path = result_path
    return True


def _drain_stderr_async(process: subprocess.Popen) -> list[bytes]:
    """Le o stderr do processo continuamente numa thread, ate ele fechar
    (processo saiu). Guarda so os ultimos ~4000 bytes, so pra diagnostico."""
    buffer: list[bytes] = [b""]

    def _drain() -> None:
        try:
            assert process.stderr is not None
            while True:
                chunk = process.stderr.read(4096)
                if not chunk:
                    break
                buffer[0] = (buffer[0] + chunk)[-4000:]
        except (OSError, ValueError):
            pass

    threading.Thread(target=_drain, daemon=True).start()
    return buffer


def _kill_ptz_process(process: subprocess.Popen) -> None:
    try:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
    except OSError:
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


def ptz_hold_stop(token: str, owner_id: int) -> None:
    session = get_session(token, owner_id)

    code = session.active_ptz_code
    session.active_ptz_code = None
    if code:
        try:
            _intelbras_client(
                host=session.host, port=session.port,
                username=session.username, password=session.password,
            ).ptz_stop_code(channel=session.channel, code=code)
        except (DahuaHttpApiError, requests_exceptions.RequestException) as exc:
            raise HikSdkLabError(f"Falha ao parar movimento PTZ via API HTTP Intelbras: {exc}") from exc
        return

    _terminate_session_ptz(session)


def _onvif_ptz_for_session(session: HikSdkLabSession):
    """Abre o servico ONVIF apenas para consultar/acionar presets.

    O movimento continua usando o SDK selecionado no laboratorio. Presets
    sao uma operacao padrao ONVIF e nao fazem parte do worker proprietario.
    """
    from onvif import ONVIFCamera

    # A sessao SDK usa 8000 (Hikvision) ou 37777 (Dahua); ONVIF normalmente
    # fica exposto na porta HTTP 80 nesses equipamentos.
    onvif_port = 80 if int(session.port) in {8000, 37777} else int(session.port)
    try:
        camera = ONVIFCamera(
            session.host,
            onvif_port,
            session.username,
            session.password,
            wsdl_dir=get_wsdl_dir(),
            transport=onvif_transport(),
        )
        _rewrite_xaddrs(camera, session.host, onvif_port)
        media = camera.create_media_service()
        profiles = media.GetProfiles() or []
        profile = next(
            (item for item in profiles if getattr(item, "PTZConfiguration", None) is not None),
            profiles[0] if profiles else None,
        )
        token = str(getattr(profile, "token", "") or "") if profile is not None else ""
        if not token:
            raise HikSdkLabError("A câmera não retornou um profile ONVIF para presets.")
        return camera.create_ptz_service(), token
    except HikSdkLabError:
        raise
    except Exception as exc:
        msg = str(exc)
        if "Sender not Authorized" in msg or "Invalid username or password" in msg:
            raise HikSdkLabError(
                f"Credenciais ONVIF recusadas pela câmera ({session.host}:{onvif_port}). "
                "Crie/habilite o usuário ONVIF na interface web da câmera (aba Integração/Protocolos)."
            ) from exc
        raise HikSdkLabError(f"Falha no serviço ONVIF em {session.host}:{onvif_port}: {exc}") from exc


def list_presets(token: str, owner_id: int) -> list[dict[str, str]]:
    session = get_session(token, owner_id)
    if session.manufacturer == "intelbras" and session.connection_mode == "sdk":
        try:
            return _intelbras_client(
                host=session.host,
                port=session.port,
                username=session.username,
                password=session.password,
            ).get_presets(channel=session.channel)
        except Exception as exc:
            raise HikSdkLabError(
                f"Nao foi possivel listar presets via API HTTP Intelbras: {exc}"
            ) from exc
    if session.manufacturer in {"dahua", "hikvision"} and session.connection_mode == "sdk":
        try:
            result = _run_worker(
                "list_presets",
                _credentials(session),
                manufacturer=session.manufacturer,
            )
            return list(result.get("presets") or [])
        except Exception as exc:
            if session.connection_mode == "sdk":
                raise HikSdkLabError(
                    f"Nao foi possivel listar os presets via SDK {session.manufacturer.capitalize()}: {exc}"
                ) from exc

    try:
        ptz, profile_token = _onvif_ptz_for_session(session)
        raw = ptz.GetPresets({"ProfileToken": profile_token}) or []
        result = []
        for item in raw:
            preset_token = str(getattr(item, "token", "") or "").strip()
            if not preset_token:
                continue
            result.append({
                "token": preset_token,
                "name": str(getattr(item, "Name", "") or f"Preset {preset_token}"),
            })
        return result
    except HikSdkLabError:
        raise
    except Exception as exc:
        msg = str(exc)
        if "Sender not Authorized" in msg or "Invalid username or password" in msg:
            raise HikSdkLabError(
                f"Credenciais ONVIF recusadas pela câmera ({session.host}). "
                "Crie/habilite o usuário ONVIF na interface web da câmera (aba Integração/Protocolos)."
            ) from exc
        raise HikSdkLabError(f"Nao foi possivel listar os presets via ONVIF: {exc}") from exc


def goto_preset(token: str, owner_id: int, preset_token: str) -> None:
    session = get_session(token, owner_id)
    preset_token = str(preset_token or "").strip()
    if not preset_token:
        raise HikSdkLabError("Informe o token do preset.")

    if session.connection_mode == "sdk":
        if session.manufacturer == "intelbras":
            try:
                _intelbras_client(
                    host=session.host,
                    port=session.port,
                    username=session.username,
                    password=session.password,
                ).goto_preset(channel=session.channel, preset_token=preset_token)
                return
            except Exception as exc:
                raise HikSdkLabError(
                    f"Nao foi possivel acionar o preset via API HTTP Intelbras: {exc}"
                ) from exc
        if session.manufacturer in {"dahua", "hikvision"}:
            _run_worker(
                "goto_preset",
                {**_credentials(session), "preset_token": preset_token},
                manufacturer=session.manufacturer,
            )
            return

    try:
        ptz, profile_token = _onvif_ptz_for_session(session)
        ptz.GotoPreset({"ProfileToken": profile_token, "PresetToken": preset_token})
    except HikSdkLabError:
        raise
    except Exception as exc:
        msg = str(exc)
        if "Sender not Authorized" in msg or "Invalid username or password" in msg:
            raise HikSdkLabError(
                f"Credenciais ONVIF recusadas pela câmera ({session.host}). "
                "Crie/habilite o usuário ONVIF na interface web da câmera (aba Integração/Protocolos)."
            ) from exc
        raise HikSdkLabError(f"Nao foi possivel acionar o preset via ONVIF: {exc}") from exc
