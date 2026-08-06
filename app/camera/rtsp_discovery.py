from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import subprocess
from urllib.parse import quote, urlunsplit

import cv2

from app.camera.rtsp_capture import RTSPCapture
from app.core.url_safety import mask_url_credentials


COMMON_RTSP_PORTS = (554, 8554, 10554, 80)
COMMON_RTSP_PATHS = (
    "/Streaming/Channels/101",
    "/Streaming/Channels/1",
    "/cam/realmonitor?channel=1&subtype=0",
    "/cam/realmonitor?channel=1&subtype=1",
    "/stream1",
    "/stream2",
    "/live/ch00_0",
    "/live/ch00_1",
)


def _encode_credentials(username: str, password: str) -> str:
    safe_user = quote(str(username or "").strip(), safe="")
    safe_pass = quote(str(password or "").strip(), safe="")

    if safe_user and safe_pass:
        return f"{safe_user}:{safe_pass}@"
    if safe_user:
        return f"{safe_user}@"
    return ""


def build_rtsp_url(ip: str, port: int, path: str, username: str, password: str) -> str:
    raw_path = str(path or "").strip()
    if not raw_path.startswith("/"):
        raw_path = f"/{raw_path}"

    parsed_path, _, query = raw_path.partition("?")
    netloc = f"{_encode_credentials(username, password)}{ip}:{int(port)}"

    return urlunsplit(("rtsp", netloc, parsed_path, query, ""))


def build_common_rtsp_candidates(
    ip: str,
    username: str,
    password: str,
    ports: tuple[int, ...] = COMMON_RTSP_PORTS,
    paths: tuple[str, ...] = COMMON_RTSP_PATHS,
) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    for port in ports:
        for path in paths:
            candidate = build_rtsp_url(ip=ip, port=port, path=path, username=username, password=password)
            if candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)

    return candidates


def _safe_capture_prop(capture: RTSPCapture, prop: int) -> float | None:
    try:
        if capture.cap is None:
            return None
        value = float(capture.cap.get(prop) or 0.0)
        return value if value > 0 else None
    except Exception:
        return None


def probe_rtsp_url_details(
    rtsp_url: str,
    *,
    allow_transport_fallback: bool = True,
) -> dict[str, str | bool | int | float | None]:
    # This is an explicit, short-lived connectivity test from the discovery UI,
    # not a runtime worker. It must still work when workers run in gateway-only
    # mode, otherwise every valid URL is reported as failed before OpenCV tries it.
    capture_kwargs = {"allow_gateway_exclusive_probe": True}
    if not allow_transport_fallback:
        capture_kwargs["allow_transport_fallback"] = False
    capture = RTSPCapture(rtsp_url, **capture_kwargs)

    try:
        capture.open()
        ok, frame = capture.read_latest(drop_frames=0)
        if not ok or frame is None:
            return {"ok": False, "error": "RTSP abriu, mas nenhum frame foi lido"}

        prop_width = _safe_capture_prop(capture, cv2.CAP_PROP_FRAME_WIDTH)
        prop_height = _safe_capture_prop(capture, cv2.CAP_PROP_FRAME_HEIGHT)
        prop_fps = _safe_capture_prop(capture, cv2.CAP_PROP_FPS)
        frame_height, frame_width = frame.shape[:2]

        return {
            "ok": True,
            "error": "",
            "width": int(prop_width or frame_width or 0),
            "height": int(prop_height or frame_height or 0),
            "fps": round(float(prop_fps), 2) if prop_fps else None,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            capture.release()
        except Exception:
            pass


def probe_rtsp_url(rtsp_url: str) -> tuple[bool, str | None]:
    details = probe_rtsp_url_details(rtsp_url)
    return bool(details.get("ok")), str(details.get("error") or "") or None


def probe_rtsp_url_details_bounded(
    rtsp_url: str,
    *,
    timeout_seconds: float = 15.0,
    transport: str = "tcp",
) -> dict[str, str | bool | int | float | None]:
    """Read one frame in a killable FFmpeg subprocess.

    OpenCV's FFmpeg timeout options are advisory and, depending on the build or
    RTSP server, an open can outlive them by more than a minute. Discovery jobs
    need a hard deadline so they can checkpoint and continue with the next URL.
    """
    safe_timeout = max(2.0, min(60.0, float(timeout_seconds or 15.0)))
    safe_transport = str(transport or "tcp").strip().lower()
    if safe_transport not in {"tcp", "udp"}:
        safe_transport = "tcp"

    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        safe_transport,
        "-i",
        rtsp_url,
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-an",
        "-sn",
        "-dn",
        "-f",
        "null",
        "-",
    ]

    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=safe_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"Tempo limite de {safe_timeout:.0f}s ao testar o stream",
            "timed_out": True,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "FFmpeg nao esta instalado no servidor de descoberta",
            "timed_out": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Falha ao executar probe RTSP: {exc.__class__.__name__}",
            "timed_out": False,
        }

    if completed.returncode != 0:
        stderr_text = bytes(completed.stderr or b"").decode("utf-8", errors="replace").lower()
        if "401 unauthorized" in stderr_text or "authorization failed" in stderr_text:
            error = "NVR recusou o usuario ou a senha (401 Unauthorized)"
        else:
            error = f"FFmpeg nao conseguiu ler um frame RTSP (codigo {int(completed.returncode)})"
        return {
            "ok": False,
            "error": error,
            "timed_out": False,
        }

    return {
        "ok": True,
        "error": "",
        "width": None,
        "height": None,
        "fps": None,
        "timed_out": False,
    }


def probe_rtsp_candidates(
    rtsp_urls: list[str],
    *,
    max_workers: int = 1,
    allow_transport_fallback: bool = True,
) -> list[dict[str, str | bool | int | float | None]]:
    urls = list(rtsp_urls)
    workers = min(max(1, int(max_workers)), max(1, len(urls)))

    def probe_one(rtsp_url: str):
        return probe_rtsp_url_details(
            rtsp_url,
            allow_transport_fallback=allow_transport_fallback,
        )

    if workers == 1 or len(urls) <= 1:
        details_by_url = [probe_one(url) for url in urls]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rtsp-probe") as executor:
            # executor.map preserves input order even though probes finish out of order.
            details_by_url = list(executor.map(probe_one, urls))

    results: list[dict[str, str | bool | int | float | None]] = []
    for index, (rtsp_url, details) in enumerate(zip(urls, details_by_url), start=1):
        results.append(
            {
                "index": index,
                "url": rtsp_url,
                "masked_url": mask_url_credentials(rtsp_url) or rtsp_url,
                "ok": bool(details.get("ok")),
                "error": str(details.get("error") or ""),
                "width": details.get("width"),
                "height": details.get("height"),
                "fps": details.get("fps"),
            }
        )

    return results
