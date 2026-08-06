"""Cliente fino para a API HTTP CGI padrao Dahua, usada tambem pelos NVRs Intelbras.

Referencia: HTTP_API_V3_59_Intelbras.pdf (vendor-local/intelbras), autenticacao
digest RFC 7616 (secao 3.4), snapshot (4.4.2), eventos (4.9.17), download de
gravacao por intervalo (4.10.14) e controle PTZ (8.1.5).
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import requests
from requests.auth import HTTPDigestAuth


DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_EVENT_LISTEN_SECONDS = 6.0
DEFAULT_RECORDING_TIMEOUT_SECONDS = 30.0

# (pan, tilt) -> codigo de operacao do "PTZ Basic Movement" (secao 8.1.5).
_PTZ_DIRECTION_CODES: dict[tuple[int, int], str] = {
    (0, 1): "Up", (0, -1): "Down", (-1, 0): "Left", (1, 0): "Right",
    (-1, 1): "LeftUp", (1, 1): "RightUp", (-1, -1): "LeftDown", (1, -1): "RightDown",
}
_PTZ_DIAGONAL_CODES = {"LeftUp", "RightUp", "LeftDown", "RightDown"}


class DahuaHttpApiError(RuntimeError):
    """Erro ao falar com a API HTTP CGI (Dahua/Intelbras)."""


def _iso_to_device_time(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DahuaHttpApiError("Data/hora vazia.")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DahuaHttpApiError(f"Data/hora invalida: {value}") from exc
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _parse_event_line(line: str) -> dict[str, Any]:
    remainder = line
    data_part: str | None = None
    if ";data=" in remainder:
        remainder, data_part = remainder.split(";data=", 1)

    event: dict[str, Any] = {}
    for chunk in remainder.split(";"):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        event[key.strip().lower()] = value.strip()

    if data_part is not None:
        try:
            event["data"] = json.loads(data_part)
        except ValueError:
            event["data"] = data_part

    return event


class DahuaHttpApiClient:
    """Chama a API HTTP CGI do dispositivo (magicBox/snapshot/eventManager/loadfile)."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 80,
        username: str = "",
        password: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        preset_channel_one_based: bool = False,
    ):
        self._base_url = f"http://{host}:{int(port or 80)}"
        self._auth = HTTPDigestAuth(username or "", password or "")
        self._timeout = timeout_seconds
        self._preset_channel_one_based = bool(preset_channel_one_based)

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        stream: bool = False,
        timeout: float | None = None,
    ) -> requests.Response:
        response = requests.get(
            f"{self._base_url}{path}",
            params=params,
            auth=self._auth,
            timeout=timeout if timeout is not None else self._timeout,
            stream=stream,
        )
        if response.status_code == 401:
            raise DahuaHttpApiError("Credenciais rejeitadas pelo dispositivo (401).")
        if response.status_code == 403:
            raise DahuaHttpApiError("Autenticacao digest invalida (403).")
        response.raise_for_status()
        return response

    def get_serial_number(self) -> str:
        response = self._get("/cgi-bin/magicBox.cgi", {"action": "getSerialNo"})
        text = response.text.strip()
        return text.split("=", 1)[1].strip() if "=" in text else text

    def get_snapshot(self, *, channel: int) -> bytes:
        response = self._get("/cgi-bin/snapshot.cgi", {"channel": int(channel), "type": 0})
        return response.content

    def download_recording(
        self,
        *,
        channel: int,
        start_iso: str,
        end_iso: str,
        subtype: int = 0,
        timeout_seconds: float = DEFAULT_RECORDING_TIMEOUT_SECONDS,
    ) -> bytes:
        response = self._get(
            "/cgi-bin/loadfile.cgi",
            {
                "action": "startLoad",
                "channel": int(channel),
                "startTime": _iso_to_device_time(start_iso),
                "endTime": _iso_to_device_time(end_iso),
                "subtype": int(subtype),
                "Types": "dav",
            },
            timeout=max(self._timeout, timeout_seconds),
        )
        return response.content

    def _ptz_action(self, action: str, *, channel: int, code: str, arg1: int, arg2: int, arg3: int) -> None:
        response = self._get(
            "/cgi-bin/ptz.cgi",
            {
                "action": action,
                "channel": int(channel),
                "code": code,
                "arg1": int(arg1),
                "arg2": int(arg2),
                "arg3": int(arg3),
            },
        )
        body = response.text.strip()
        print(f"[SDK-LAB] _ptz_action action={action} code={code} channel={channel} args=({arg1},{arg2},{arg3}) body={body!r}")
        if body != "OK":
            raise DahuaHttpApiError(f"PTZ {action} falhou: {body or 'sem resposta do dispositivo'}")

    @staticmethod
    def _ptz_code_and_args(*, pan: int, tilt: int, zoom: int, speed: int) -> tuple[str, int, int]:
        if zoom > 0:
            return "ZoomTele", 0, 0
        if zoom < 0:
            return "ZoomWide", 0, 0
        code = _PTZ_DIRECTION_CODES.get((pan, tilt))
        if code is None:
            raise DahuaHttpApiError("Direcao PTZ invalida.")
        arg1, arg2 = (speed, speed) if code in _PTZ_DIAGONAL_CODES else (0, speed)
        return code, arg1, arg2

    def ptz_move(
        self,
        *,
        channel: int,
        pan: int = 0,
        tilt: int = 0,
        zoom: int = 0,
        speed: int = 4,
        duration_ms: int = 300,
    ) -> None:
        code, arg1, arg2 = self._ptz_code_and_args(pan=pan, tilt=tilt, zoom=zoom, speed=speed)
        self._ptz_action("start", channel=channel, code=code, arg1=arg1, arg2=arg2, arg3=0)
        try:
            time.sleep(duration_ms / 1000)
        finally:
            self._ptz_action("stop", channel=channel, code=code, arg1=0, arg2=0, arg3=0)

    def ptz_start(
        self,
        *,
        channel: int,
        pan: int = 0,
        tilt: int = 0,
        zoom: int = 0,
        speed: int = 4,
    ) -> str:
        """Inicia um movimento continuo (o dispositivo nao para sozinho).

        Ao contrario de ptz_move, nao bloqueia a thread com sleep nem manda o
        "stop" automaticamente. O chamador deve guardar o "code" retornado e
        chamar ptz_stop_code quando o usuario soltar o botao.
        """
        code, arg1, arg2 = self._ptz_code_and_args(pan=pan, tilt=tilt, zoom=zoom, speed=speed)
        self._ptz_action("start", channel=channel, code=code, arg1=arg1, arg2=arg2, arg3=0)
        return code

    def ptz_stop_code(self, *, channel: int, code: str) -> None:
        self._ptz_action("stop", channel=channel, code=code, arg1=0, arg2=0, arg3=0)

    def get_presets(self, *, channel: int = 1) -> list[dict[str, str]]:
        cgi_channel = self._preset_cgi_channel(channel)
        try:
            response = self._get("/cgi-bin/ptz.cgi", {"action": "getPresets", "channel": cgi_channel})
            text = response.text.strip()
            presets_map: dict[str, dict[str, str]] = {}
            for line in text.splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                if "." in key:
                    prefix, field = key.rsplit(".", 1)
                    field_lower = field.lower()
                    if prefix not in presets_map:
                        presets_map[prefix] = {}
                    if field_lower in {"index", "name"}:
                        presets_map[prefix][field_lower] = val.strip()
            result: list[dict[str, str]] = []
            for item in presets_map.values():
                token = item.get("index")
                name = item.get("name") or (f"Preset {token}" if token else "")
                if token:
                    result.append({"token": token, "name": name})
            return result
        except Exception as exc:
            raise DahuaHttpApiError(f"Falha ao consultar presets via CGI HTTP: {exc}") from exc

    def _preset_cgi_channel(self, channel: int) -> int:
        """Converte o canal lógico para a convenção do endpoint de presets.

        O CGI Intelbras ``ptz.cgi`` usa canais iniciados em 1, embora a árvore
        ``configManager PtzPreset[]`` use índices iniciados em 0. Alguns
        dispositivos Dahua legados esperam o canal iniciado em 0; por isso a
        escolha é explícita no cliente e o comportamento anterior é preservado
        por padrão.
        """
        logical_channel = max(1, int(channel))
        if self._preset_channel_one_based:
            return logical_channel
        return logical_channel - 1

    def goto_preset(self, *, channel: int = 1, preset_token: str) -> None:
        cgi_channel = self._preset_cgi_channel(channel)
        try:
            preset_id = int(preset_token)
        except ValueError:
            raise DahuaHttpApiError("Numero do preset invalido.")
        if not 1 <= preset_id <= 300:
            raise DahuaHttpApiError("O preset deve estar entre 1 e 300.")
        # No CGI Dahua/Intelbras, GotoPreset recebe o indice em arg2.
        # arg1 e reservado para este comando. O equipamento pode responder
        # "OK" mesmo quando o indice e enviado no campo errado, sem mover.
        self._ptz_action(
            "start",
            channel=cgi_channel,
            code="GotoPreset",
            arg1=0,
            arg2=preset_id,
            arg3=0,
        )

    def subscribe_events(
        self,
        *,
        codes: str = "All",
        channel: int | None = None,
        listen_seconds: float = DEFAULT_EVENT_LISTEN_SECONDS,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"action": "attach", "codes": f"[{codes}]", "heartbeat": 5}
        if channel is not None:
            params["channel"] = int(channel)

        events: list[dict[str, Any]] = []
        response = self._get("/cgi-bin/eventManager.cgi", params, stream=True, timeout=listen_seconds)
        deadline = time.monotonic() + listen_seconds
        try:
            for line in response.iter_lines(decode_unicode=True):
                if time.monotonic() > deadline:
                    break
                if not line or not line.startswith("Code="):
                    continue
                events.append(_parse_event_line(line))
        except requests.exceptions.Timeout:
            pass
        finally:
            response.close()
        return events
