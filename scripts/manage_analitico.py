#!/usr/bin/env python3
"""Gerenciador multiplataforma de instalacao e atualizacao do Analitico."""

from __future__ import annotations

import argparse
import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence


PROJECT_DIR = Path(__file__).resolve().parents[1]


class ManagerError(RuntimeError):
    pass


def _run(
    command: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("+", subprocess.list2cmdline(list(command)))
    return subprocess.run(
        list(command),
        cwd=PROJECT_DIR,
        env=env,
        check=True,
        text=True,
        capture_output=capture,
    )


def _output(command: Sequence[str]) -> str:
    try:
        return _run(command, capture=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _require_tools(*tools: str) -> None:
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise ManagerError(
            "Ferramentas obrigatorias nao encontradas: " + ", ".join(missing)
        )


def detect_primary_lan_ipv4() -> str:
    for target in ("1.1.1.1", "8.8.8.8"):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((target, 80))
            candidate = str(probe.getsockname()[0])
            address = ipaddress.ip_address(candidate)
            if not address.is_loopback and not address.is_link_local:
                return candidate
        except (OSError, ValueError):
            pass
        finally:
            probe.close()

    try:
        candidates = socket.getaddrinfo(
            socket.gethostname(), None, family=socket.AF_INET
        )
    except OSError:
        candidates = []
    for candidate in candidates:
        value = str(candidate[4][0])
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if not address.is_loopback and not address.is_link_local:
            return value
    return ""


def _dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def compose_environment() -> dict[str, str]:
    # Compose interpolates ${VAR} before applying service env_file entries.
    # Load deployment values first; explicit process variables still win.
    environment = _dotenv_values(PROJECT_DIR / ".env.docker")
    environment.update(os.environ)
    configured = environment.get("MTX_WEBRTCADDITIONALHOSTS", "").strip()
    if configured:
        print(f"WebRTC ICE: usando override {configured}")
        return environment

    detected = detect_primary_lan_ipv4()
    if not detected:
        raise ManagerError(
            "Nao foi possivel detectar o IPv4 LAN para o WebRTC. "
            "Defina MTX_WEBRTCADDITIONALHOSTS somente para redes especiais."
        )
    environment["MTX_WEBRTCADDITIONALHOSTS"] = detected
    print(f"WebRTC ICE: IPv4 LAN detectado automaticamente: {detected}")
    return environment


def _host_has_nvidia() -> bool:
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        subprocess.run(
            ["nvidia-smi", "-L"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _docker_has_nvidia() -> bool:
    runtimes = _output(["docker", "info", "--format", "{{json .Runtimes}}"])
    return '"nvidia"' in runtimes


def select_accelerator(requested: str) -> tuple[str, str]:
    requested = requested.lower()
    host_nvidia = _host_has_nvidia()
    docker_nvidia = _docker_has_nvidia()
    if requested == "nvidia":
        if not host_nvidia:
            raise ManagerError("NVIDIA solicitada, mas nvidia-smi nao detectou GPU.")
        if not docker_nvidia:
            raise ManagerError("NVIDIA solicitada, mas o runtime Docker nao existe.")
        return "nvidia", "forcado pelo operador"
    if requested == "cpu":
        return "cpu", "forcado pelo operador"
    if host_nvidia and docker_nvidia:
        return "nvidia", "GPU e runtime NVIDIA detectados automaticamente"
    if host_nvidia:
        return "cpu", "GPU detectada, mas runtime NVIDIA indisponivel"
    return "cpu", "GPU NVIDIA utilizavel nao detectada"


def compose_command(profile: str, *arguments: str) -> list[str]:
    command = [
        "docker",
        "compose",
        "--env-file",
        str(PROJECT_DIR / ".env.docker"),
        "-f",
        str(PROJECT_DIR / "docker-compose.yml"),
    ]
    if profile == "nvidia":
        command.extend(["-f", str(PROJECT_DIR / "docker-compose.gpu.yml")])
    command.extend(arguments)
    return command


def ensure_local_environment() -> None:
    target = PROJECT_DIR / ".env.docker"
    if target.exists():
        print(f"Configuracao local preservada: {target}")
        return
    source = PROJECT_DIR / ".env.docker.example"
    if not source.exists():
        raise ManagerError(f"Modelo de configuracao nao encontrado: {source}")
    shutil.copy2(source, target)
    print(f"Configuracao inicial criada: {target}")


def update_repository() -> None:
    _require_tools("git")
    tracked_changes = _output(
        ["git", "status", "--porcelain", "--untracked-files=no"]
    )
    if tracked_changes:
        raise ManagerError(
            "Existem mudancas locais rastreadas. Resolva-as antes da atualizacao."
        )

    upstream = _output(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    )
    if upstream and "/" in upstream:
        remote, branch = upstream.split("/", 1)
    else:
        branch = _output(["git", "branch", "--show-current"])
        remote = "origin"
    if not branch:
        raise ManagerError("Nao foi possivel determinar a branch atual.")

    _run(["git", "fetch", remote, branch])
    _run(["git", "pull", "--ff-only", remote, branch])


def wait_http(url: str, name: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                if 200 <= int(response.status) < 400:
                    print(f"{name} pronto: {url}")
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(2)
    raise ManagerError(f"{name} nao respondeu em {timeout_seconds}s: {url}")


def run_stack(
    profile: str,
    environment: dict[str, str],
    *,
    build: bool,
    force_recreate: bool,
    wait: bool,
) -> None:
    arguments = ["up", "-d"]
    if force_recreate:
        arguments.append("--force-recreate")
    if build:
        arguments.append("--build")
    _run(compose_command(profile, *arguments), env=environment)
    if wait:
        wait_http("http://localhost:8090/healthz", "camera-gateway", 180)
        wait_http("http://localhost:8000/monitor", "analitico", 240)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Instala, atualiza e controla o Analitico no Windows ou Linux."
    )
    parser.add_argument(
        "command",
        choices=("install", "update", "up", "restart", "stop", "status", "profile"),
    )
    parser.add_argument(
        "--accelerator",
        choices=("auto", "cpu", "nvidia"),
        default=os.getenv("ANALITICO_ACCELERATOR", "auto"),
    )
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _require_tools("docker")
    environment = compose_environment()
    profile, reason = select_accelerator(args.accelerator)
    print(f"Perfil de aceleracao: {profile} ({reason})")

    if args.command == "profile":
        print(f"webrtc_additional_hosts={environment['MTX_WEBRTCADDITIONALHOSTS']}")
        return 0
    if args.command == "status":
        _run(compose_command(profile, "ps"), env=environment)
        return 0
    if args.command == "stop":
        _run(compose_command(profile, "down"), env=environment)
        return 0

    if args.command == "update":
        update_repository()
    if args.command in {"install", "update"}:
        ensure_local_environment()

    run_stack(
        profile,
        environment,
        build=not args.no_build,
        force_recreate=args.command == "restart",
        wait=not args.no_wait,
    )
    print("Tudo pronto. Monitor: http://localhost:8000/monitor")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ManagerError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except subprocess.CalledProcessError as exc:
        print(f"ERRO: comando terminou com codigo {exc.returncode}.", file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
