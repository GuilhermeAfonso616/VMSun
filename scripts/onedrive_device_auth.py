from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings


SCOPES = "offline_access Files.ReadWrite.AppFolder"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autoriza o OneDrive via device code e salva o token localmente.")
    parser.add_argument("--client-id", default=settings.onedrive_client_id, help="Application (client) ID do app registrado no Entra.")
    parser.add_argument(
        "--tenant",
        default=settings.onedrive_tenant,
        help="Use o tenant ID/dominio para app single-tenant corporativo; organizations para multi-tenant corporativo; consumers para conta pessoal.",
    )
    parser.add_argument("--token-file", default=settings.onedrive_token_file, help="Arquivo JSON onde o token sera salvo.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.client_id:
        raise SystemExit("Informe --client-id ou configure ONEDRIVE_CLIENT_ID.")

    device_response = requests.post(
        f"https://login.microsoftonline.com/{args.tenant}/oauth2/v2.0/devicecode",
        data={"client_id": args.client_id, "scope": SCOPES},
        timeout=30,
    )
    device_response.raise_for_status()
    device_payload = device_response.json()
    print(device_payload.get("message") or f"Acesse {device_payload.get('verification_uri')} e digite {device_payload.get('user_code')}")

    interval = int(device_payload.get("interval") or 5)
    deadline = time.time() + int(device_payload.get("expires_in") or 900)
    while time.time() < deadline:
        time.sleep(interval)
        token_response = requests.post(
            f"https://login.microsoftonline.com/{args.tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": args.client_id,
                "device_code": device_payload["device_code"],
            },
            timeout=30,
        )
        payload = token_response.json()
        if token_response.ok:
            expires_in = int(payload.get("expires_in") or 3600)
            payload["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 60))).isoformat()
            token_file = Path(args.token_file)
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Token salvo em: {token_file}")
            return 0

        error = payload.get("error")
        if error in {"authorization_pending", "slow_down"}:
            if error == "slow_down":
                interval += 5
            continue
        raise SystemExit(f"Falha na autorizacao: {payload}")

    raise SystemExit("Tempo expirado antes da autorizacao.")


if __name__ == "__main__":
    raise SystemExit(main())
