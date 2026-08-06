import os
import site
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from onvif import ONVIFCamera
from zeep.transports import Transport

from app.core.url_safety import mask_url_credentials


def onvif_transport(timeout: float = 5.0) -> Transport:
    """Transport com timeout curto pra chamadas ONVIF.

    Sem isso, o zeep usa operation_timeout=None (sem limite): conectar numa
    camera sem ONVIF (porta filtrada, host errado, firewall derrubando o
    pacote sem RST) trava por dezenas de segundos em vez de falhar rapido,
    o que deixa a deteccao de PTZ no monitor lenta pra cada camera sem ONVIF.
    """
    return Transport(timeout=timeout, operation_timeout=timeout)


@dataclass(frozen=True, slots=True)
class RTSPProfile:
    token: str
    name: str
    rtsp_url: str
    encoding: str | None = None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class RTSPDiscoveryResult:
    rtsp_url: str
    onvif_port: int
    profiles: list[RTSPProfile] = field(default_factory=list)


def get_wsdl_dir() -> str:
    candidates: list[Path] = []

    env_wsdl = os.getenv("ONVIF_WSDL_DIR", "").strip()
    if env_wsdl:
        candidates.append(Path(env_wsdl))

    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "wsdl")
        candidates.append(Path(sys.executable).resolve().parent / "wsdl")

    project_root = Path(__file__).resolve().parents[2]
    candidates.append(project_root / "wsdl")

    try:
        import onvif

        onvif_pkg = Path(onvif.__file__).resolve().parent
        candidates.append(onvif_pkg / "wsdl")
        candidates.append(onvif_pkg.parent / "wsdl")
    except Exception:
        pass

    try:
        for site_path in site.getsitepackages():
            p = Path(site_path)
            candidates.append(p / "wsdl")
            candidates.append(p / "onvif" / "wsdl")
    except Exception:
        pass

    try:
        user_site = site.getusersitepackages()
        if user_site:
            p = Path(user_site)
            candidates.append(p / "wsdl")
            candidates.append(p / "onvif" / "wsdl")
    except Exception:
        pass

    candidates.append(Path.cwd() / "wsdl")

    unique_candidates: list[Path] = []
    seen: set[str] = set()

    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key not in seen:
            seen.add(key)
            unique_candidates.append(candidate)

    for path in unique_candidates:
        if path.exists() and path.is_dir():
            print(f"[ONVIF] usando wsdl em: {path}")
            return str(path)

    tried = "\n".join(str(p) for p in unique_candidates)
    raise FileNotFoundError(f"Pasta wsdl nao encontrada. Caminhos tentados:\n{tried}")


def _env_str(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _env_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    return int(value)


def _host_resolves(host: str | None) -> bool:
    if not host:
        return False

    try:
        socket.getaddrinfo(host, None)
        return True
    except Exception:
        return False


def _override_netloc(url: str, host: str | None = None, port: int | None = None) -> str:
    parts = urlsplit(url)

    username = parts.username or ""
    password = parts.password or ""
    final_host = host or (parts.hostname or "")
    final_port = port if port is not None else parts.port

    auth = ""
    if username:
        auth = quote(username, safe="")
        if password:
            auth += f":{quote(password, safe='')}"
        auth += "@"

    hostport = final_host
    if final_port:
        hostport += f":{final_port}"

    return urlunsplit((parts.scheme, f"{auth}{hostport}", parts.path, parts.query, parts.fragment))


def _rewrite_xaddrs(camera, host: str, port: int) -> None:
    """
    Reescreve os XAddr dos servicos ONVIF para apontarem ao proxy do host.
    """

    rewritten = {}

    for ns, xaddr in (getattr(camera, "xaddrs", {}) or {}).items():
        try:
            new_xaddr = _override_netloc(xaddr, host=host, port=port)
            rewritten[ns] = new_xaddr
            print(f"[ONVIF] XAddr {ns} -> {new_xaddr}")
        except Exception:
            rewritten[ns] = xaddr
            print(f"[ONVIF] XAddr {ns} mantido -> {xaddr}")

    camera.xaddrs = rewritten


def inject_rtsp_credentials(rtsp_url: str, username: str, password: str) -> str:
    parts = urlsplit(rtsp_url)

    safe_user = quote(username, safe="")
    safe_pass = quote(password, safe="")

    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{safe_user}:{safe_pass}@{host}{port}"

    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _format_onvif_error(exc: Exception) -> str:
    parts = [f"{exc.__class__.__name__}: {exc}"]

    detail = getattr(exc, "detail", None)
    if detail is not None:
        parts.append(f"detail={detail}")

    reason = getattr(exc, "reason", None)
    if reason is not None:
        parts.append(f"reason={reason}")

    return " | ".join(parts)


def _extract_profile_metadata(profile) -> tuple[str, str | None, int | None, int | None]:
    profile_name = str(getattr(profile, "Name", "") or "").strip()
    profile_token = str(getattr(profile, "token", "") or "").strip()

    video_cfg = getattr(profile, "VideoEncoderConfiguration", None)
    encoding = None
    width = None
    height = None

    if video_cfg is not None:
        encoding_raw = getattr(video_cfg, "Encoding", None)
        if encoding_raw is not None:
            encoding = str(encoding_raw)

        resolution = getattr(video_cfg, "Resolution", None)
        if resolution is not None:
            try:
                width_value = getattr(resolution, "Width", None)
                height_value = getattr(resolution, "Height", None)
                width = int(width_value) if width_value is not None else None
                height = int(height_value) if height_value is not None else None
            except Exception:
                width = None
                height = None

    if not profile_name:
        profile_name = profile_token or "Canal"

    return profile_name, encoding, width, height


def _apply_rtsp_proxy_if_needed(rtsp_url: str) -> str:
    rtsp_proxy_host = _env_str("RTSP_PROXY_HOST")
    rtsp_proxy_port = _env_int("RTSP_PROXY_PORT")

    if rtsp_proxy_host or rtsp_proxy_port is not None:
        return _override_netloc(rtsp_url, host=rtsp_proxy_host, port=rtsp_proxy_port)

    return rtsp_url


def _build_onvif_port_candidates(port: int | None) -> list[int]:
    candidates: list[int] = []

    if port is not None:
        normalized = int(port)
        if normalized > 0:
            candidates.append(normalized)

    for fallback in (80, 8000, 8080, 8899, 554):
        if fallback not in candidates:
            candidates.append(fallback)

    return candidates


def _discover_rtsp_url_once(
    ip: str,
    port: int,
    username: str,
    password: str,
    wsdl_dir: str,
) -> RTSPDiscoveryResult:
    onvif_connect_host = _env_str("ONVIF_CONNECT_HOST") or ip
    onvif_connect_port = _env_int("ONVIF_CONNECT_PORT") or int(port)

    if onvif_connect_host == "host.docker.internal" and not _host_resolves(onvif_connect_host):
        print(
            "[ONVIF] host.docker.internal nao resolveu neste ambiente; "
            f"usando IP da camera {ip} para a descoberta ONVIF"
        )
        onvif_connect_host = ip

    print(
        f"[ONVIF] conectando em host={onvif_connect_host} "
        f"port={onvif_connect_port} camera_ip_original={ip}"
    )

    camera = ONVIFCamera(
        onvif_connect_host,
        onvif_connect_port,
        username,
        password,
        wsdl_dir=wsdl_dir,
        transport=onvif_transport(),
    )

    _rewrite_xaddrs(camera, onvif_connect_host, onvif_connect_port)

    media_service = camera.create_media_service()
    profiles = media_service.GetProfiles()

    if not profiles:
        raise RuntimeError("Nenhum profile ONVIF encontrado.")

    discovered_profiles: list[RTSPProfile] = []

    for profile in profiles:
        profile_token = str(getattr(profile, "token", "") or "").strip()
        if not profile_token:
            continue

        stream_setup = {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}}

        req = media_service.create_type("GetStreamUri")
        req.StreamSetup = stream_setup
        req.ProfileToken = profile_token

        try:
            stream_uri = media_service.GetStreamUri(req)
        except Exception as exc:
            print(f"[ONVIF] falha ao obter stream do profile {profile_token}: {exc}")
            continue

        if not stream_uri or not getattr(stream_uri, "Uri", None):
            print(f"[ONVIF] profile {profile_token} sem URI RTSP")
            continue

        raw_uri = stream_uri.Uri
        final_uri = inject_rtsp_credentials(raw_uri, username, password)
        final_uri = _apply_rtsp_proxy_if_needed(final_uri)

        profile_name, encoding, width, height = _extract_profile_metadata(profile)

        print(f"[ONVIF] RTSP descoberto profile={profile_name} token={profile_token}: {mask_url_credentials(raw_uri)}")
        print(f"[ONVIF] RTSP final profile={profile_name}: {mask_url_credentials(final_uri)}")

        discovered_profiles.append(
            RTSPProfile(
                token=profile_token,
                name=profile_name,
                rtsp_url=final_uri,
                encoding=encoding,
                width=width,
                height=height,
            )
        )

    if not discovered_profiles:
        raise RuntimeError("Nao foi possivel obter a URI RTSP de nenhum canal/profile ONVIF.")

    return RTSPDiscoveryResult(
        rtsp_url=discovered_profiles[0].rtsp_url,
        onvif_port=int(port),
        profiles=discovered_profiles,
    )


def discover_rtsp(ip: str, port: int | None, username: str, password: str) -> RTSPDiscoveryResult:
    wsdl_dir = get_wsdl_dir()
    forced_connect_port = _env_int("ONVIF_CONNECT_PORT")
    candidate_ports = [forced_connect_port] if forced_connect_port is not None else _build_onvif_port_candidates(port)

    errors: list[str] = []

    for candidate_port in candidate_ports:
        try:
            return _discover_rtsp_url_once(
                ip=ip,
                port=int(candidate_port),
                username=username,
                password=password,
                wsdl_dir=wsdl_dir,
            )
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            errors.append(f"porta {candidate_port}: {message}")
            print(f"[ONVIF] falha ao descobrir RTSP em {ip}:{candidate_port} -> {message}")

    attempted_ports = ", ".join(str(item) for item in candidate_ports)
    hint = ""
    if (port is not None and int(port) == 554) and forced_connect_port is None:
        hint = (
            " Dica: a porta 554 geralmente e do stream RTSP; o servico ONVIF "
            "da camera costuma responder em 80, 8000, 8080 ou 8899."
        )

    details = " | ".join(errors)
    raise RuntimeError(
        f"Nao foi possivel descobrir o RTSP via ONVIF em {ip}. "
        f"Portas tentadas: {attempted_ports}.{hint} Detalhes: {details}"
    )


def discover_rtsp_url(ip: str, port: int | None, username: str, password: str) -> str:
    return discover_rtsp(ip=ip, port=port, username=username, password=password).rtsp_url
