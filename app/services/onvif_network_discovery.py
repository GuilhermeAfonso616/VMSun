from __future__ import annotations

import http.client
import ipaddress
import select
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree


WS_DISCOVERY_ADDRESS = ("239.255.255.250", 3702)
DEFAULT_ONVIF_PORTS = (80, 8000, 8080, 8899)
MAX_DISCOVERY_ADDRESSES = 256
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


@dataclass(frozen=True, slots=True)
class OnvifNetworkDevice:
    ip: str
    port: int
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    xaddr: str = ""
    source: str = "port_scan"

    @property
    def suggested_name(self) -> str:
        return self.name or self.model or f"Camera {self.ip}"


@dataclass(frozen=True, slots=True)
class OnvifNetworkDiscoveryResult:
    network: str
    devices: list[OnvifNetworkDevice]
    ws_discovery_count: int
    port_scan_count: int
    elapsed_seconds: float


def parse_private_ipv4_network(value: str) -> ipaddress.IPv4Network:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Informe uma faixa de rede, por exemplo 192.168.1.0/24.")

    try:
        network = ipaddress.ip_network(raw, strict=False)
    except ValueError as exc:
        raise ValueError("Faixa de rede invalida. Use o formato 192.168.1.0/24.") from exc

    if not isinstance(network, ipaddress.IPv4Network):
        raise ValueError("A descoberta aceita somente redes IPv4.")
    if network.num_addresses > MAX_DISCOVERY_ADDRESSES:
        raise ValueError(
            f"A faixa pode conter no maximo {MAX_DISCOVERY_ADDRESSES} enderecos. "
            "Use uma rede /24 ou menor."
        )
    if not any(network.subnet_of(private_network) for private_network in _PRIVATE_NETWORKS):
        raise ValueError("A descoberta e permitida somente em redes IPv4 privadas.")
    return network


def network_for_camera_ip(value: str) -> str | None:
    try:
        address = ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None
    if not isinstance(address, ipaddress.IPv4Address):
        return None
    if not any(address in private_network for private_network in _PRIVATE_NETWORKS):
        return None
    return str(ipaddress.ip_network(f"{address}/24", strict=False))


def _scope_values(scopes_text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_scope in str(scopes_text or "").split():
        decoded = unquote(raw_scope)
        marker = "onvif://www.onvif.org/"
        if not decoded.lower().startswith(marker):
            continue
        remainder = decoded[len(marker) :]
        key, separator, value = remainder.partition("/")
        if separator and value and key.lower() not in values:
            values[key.lower()] = value.replace("_", " ").strip()[:120]
    return values


def _child_text(element: ElementTree.Element, local_name: str) -> str:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1] == local_name and child.text:
            return child.text.strip()
    return ""


def parse_ws_discovery_response(
    payload: bytes,
    *,
    network: ipaddress.IPv4Network | None = None,
) -> list[OnvifNetworkDevice]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return []

    devices: list[OnvifNetworkDevice] = []
    for match in root.iter():
        if match.tag.rsplit("}", 1)[-1] != "ProbeMatch":
            continue

        xaddrs = _child_text(match, "XAddrs").split()
        scopes = _scope_values(_child_text(match, "Scopes"))
        for xaddr in xaddrs:
            try:
                parsed = urlsplit(xaddr)
                address = ipaddress.ip_address(parsed.hostname or "")
                port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
            except (ValueError, TypeError):
                continue
            if not isinstance(address, ipaddress.IPv4Address):
                continue
            if network is not None and address not in network:
                continue

            devices.append(
                OnvifNetworkDevice(
                    ip=str(address),
                    port=port,
                    name=scopes.get("name", ""),
                    model=scopes.get("hardware", ""),
                    xaddr=xaddr[:512],
                    source="ws_discovery",
                )
            )
            break
    return devices


def _ws_discovery_probe_xml() -> bytes:
    message_id = f"urn:uuid:{uuid.uuid4()}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>{message_id}</w:MessageID>
    <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>
</e:Envelope>""".encode("utf-8")


def discover_via_ws_discovery(
    network: ipaddress.IPv4Network,
    *,
    timeout_seconds: float = 2.5,
) -> list[OnvifNetworkDevice]:
    timeout_seconds = min(8.0, max(0.5, float(timeout_seconds)))
    deadline = time.monotonic() + timeout_seconds
    devices: list[OnvifNetworkDevice] = []

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.setblocking(False)
        probe = _ws_discovery_probe_xml()
        sock.sendto(probe, WS_DISCOVERY_ADDRESS)
        sock.sendto(probe, WS_DISCOVERY_ADDRESS)

        while time.monotonic() < deadline:
            wait_seconds = min(0.25, max(0.0, deadline - time.monotonic()))
            readable, _, _ = select.select([sock], [], [], wait_seconds)
            if not readable:
                continue
            try:
                payload, _ = sock.recvfrom(65535)
            except BlockingIOError:
                continue
            devices.extend(parse_ws_discovery_response(payload, network=network))
    except OSError:
        # Docker bridge networks commonly block multicast. The bounded port scan
        # below remains available as the deterministic fallback.
        return []
    finally:
        sock.close()
    return _deduplicate_devices(devices)


_DEVICE_INFORMATION_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
    <tds:GetDeviceInformation/>
  </s:Body>
</s:Envelope>"""


def _xml_value(payload: bytes, local_name: str) -> str:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return ""
    return _child_text(root, local_name)


def probe_onvif_endpoint(ip: str, port: int, *, timeout_seconds: float = 0.4) -> OnvifNetworkDevice | None:
    connection = http.client.HTTPConnection(ip, int(port), timeout=max(0.15, float(timeout_seconds)))
    try:
        connection.request(
            "POST",
            "/onvif/device_service",
            body=_DEVICE_INFORMATION_BODY,
            headers={
                "Content-Type": "application/soap+xml; charset=utf-8",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        payload = response.read(65536)
        headers = " ".join(f"{key}: {value}" for key, value in response.getheaders()).lower()
    except (OSError, http.client.HTTPException):
        return None
    finally:
        connection.close()

    body_hint = payload.lower()
    looks_like_onvif = any(
        marker in body_hint
        for marker in (b"onvif.org", b"getdeviceinformation", b":notauthorized", b"<soap", b"<s:envelope")
    )
    auth_challenge = response.status in {401, 403} and "www-authenticate" in headers
    if response.status == 404 or not (looks_like_onvif or auth_challenge):
        return None

    manufacturer = _xml_value(payload, "Manufacturer")[:120]
    model = _xml_value(payload, "Model")[:120]
    return OnvifNetworkDevice(
        ip=ip,
        port=int(port),
        manufacturer=manufacturer,
        model=model,
        xaddr=f"http://{ip}:{int(port)}/onvif/device_service",
        source="port_scan",
    )


def discover_via_port_scan(
    network: ipaddress.IPv4Network,
    *,
    ports: tuple[int, ...] = DEFAULT_ONVIF_PORTS,
    timeout_seconds: float = 0.4,
    max_workers: int = 64,
) -> list[OnvifNetworkDevice]:
    targets = [(str(address), int(port)) for address in network.hosts() for port in ports]
    if not targets:
        return []

    devices: list[OnvifNetworkDevice] = []
    workers = min(max(1, int(max_workers)), len(targets))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="onvif-scan") as executor:
        futures = {
            executor.submit(probe_onvif_endpoint, ip, port, timeout_seconds=timeout_seconds): (ip, port)
            for ip, port in targets
        }
        for future in as_completed(futures):
            try:
                device = future.result()
            except Exception:
                device = None
            if device is not None:
                devices.append(device)
    return _deduplicate_devices(devices)


def _device_score(device: OnvifNetworkDevice) -> tuple[int, int, int]:
    return (
        1 if device.source == "ws_discovery" else 0,
        1 if device.name else 0,
        1 if device.model or device.manufacturer else 0,
    )


def _merge_device(primary: OnvifNetworkDevice, secondary: OnvifNetworkDevice) -> OnvifNetworkDevice:
    if _device_score(secondary) > _device_score(primary):
        primary, secondary = secondary, primary
    return replace(
        primary,
        name=primary.name or secondary.name,
        manufacturer=primary.manufacturer or secondary.manufacturer,
        model=primary.model or secondary.model,
        xaddr=primary.xaddr or secondary.xaddr,
    )


def _deduplicate_devices(devices: list[OnvifNetworkDevice]) -> list[OnvifNetworkDevice]:
    by_ip: dict[str, OnvifNetworkDevice] = {}
    for device in devices:
        current = by_ip.get(device.ip)
        by_ip[device.ip] = device if current is None else _merge_device(current, device)
    return sorted(by_ip.values(), key=lambda item: ipaddress.ip_address(item.ip))


def discover_onvif_network(
    network_value: str,
    *,
    include_port_scan: bool = True,
    ws_timeout_seconds: float = 2.5,
    ports: tuple[int, ...] = DEFAULT_ONVIF_PORTS,
) -> OnvifNetworkDiscoveryResult:
    started_at = time.monotonic()
    network = parse_private_ipv4_network(network_value)
    ws_devices = discover_via_ws_discovery(network, timeout_seconds=ws_timeout_seconds)
    scan_devices = (
        discover_via_port_scan(network, ports=ports)
        if include_port_scan
        else []
    )
    devices = _deduplicate_devices([*ws_devices, *scan_devices])
    return OnvifNetworkDiscoveryResult(
        network=str(network),
        devices=devices,
        ws_discovery_count=len(ws_devices),
        port_scan_count=len(scan_devices),
        elapsed_seconds=round(time.monotonic() - started_at, 2),
    )
