#!/usr/bin/env python3
"""Diagnostico de listagem de presets em camera Intelbras/Dahua via CGI HTTP.

Motivo: `DahuaHttpApiClient.get_presets` devolve lista vazia tanto quando a
camera nao tem presets quanto quando a resposta nao casa com o parser. Este
script separa os dois casos, mostrando a resposta crua do equipamento.

Uso (no servidor, dentro do container do runtime ou com acesso a rede da camera):

    python3 scripts/diagnostico_presets_intelbras.py \
        --host 192.168.0.50 --user admin --password 'senha' [--channel 1]

Nada e alterado na camera: todas as chamadas sao de leitura.
"""

from __future__ import annotations

import argparse
import sys

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth


def _request(base: str, path: str, params: dict, auth, timeout: float = 8.0):
    url = f"{base}{path}"
    try:
        response = requests.get(url, params=params, auth=auth, timeout=timeout)
        return response.status_code, response.text.strip()
    except Exception as exc:  # noqa: BLE001
        return None, f"<excecao: {exc.__class__.__name__}: {exc}>"


def _parse_como_o_sistema_faz(text: str) -> list[dict[str, str]]:
    """Reproduz exatamente o parser de get_presets(), para comparacao."""
    presets_map: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, val = line.split("=", 1)
        if "." in key:
            prefix, field = key.rsplit(".", 1)
            field_lower = field.lower()
            presets_map.setdefault(prefix, {})
            if field_lower in {"index", "name"}:
                presets_map[prefix][field_lower] = val.strip()
    resultado = []
    for item in presets_map.values():
        token = item.get("index")
        if token:
            resultado.append({"token": token, "name": item.get("name") or f"Preset {token}"})
    return resultado


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--channel", type=int, default=1, help="canal 1-based da camera")
    parser.add_argument("--https", action="store_true")
    args = parser.parse_args()

    base = f"{'https' if args.https else 'http'}://{args.host}:{args.port}"
    tentativas = [
        ("digest", HTTPDigestAuth(args.user, args.password)),
        ("basic", HTTPBasicAuth(args.user, args.password)),
    ]

    print(f"Camera: {base}  canal informado={args.channel}")
    print("=" * 78)

    auth_ok = None
    for nome, auth in tentativas:
        status, texto = _request(base, "/cgi-bin/magicBox.cgi", {"action": "getSerialNo"}, auth)
        print(f"[auth {nome:6s}] getSerialNo -> HTTP {status}: {texto[:80]}")
        if status == 200:
            auth_ok = (nome, auth)
            break
    if auth_ok is None:
        print("\nFALHA: nenhuma autenticacao funcionou. Verifique usuario/senha/porta.")
        return 1
    print(f"\nAutenticacao usada: {auth_ok[0]}\n")
    auth = auth_ok[1]

    # O sistema converte canal 1-based -> 0-based. Testamos os dois para saber
    # qual a firmware desta camera realmente aceita.
    print("=" * 78)
    print("TESTE 1 - getPresets em canais 0-based e 1-based")
    print("=" * 78)
    for canal_cgi in (max(0, args.channel - 1), args.channel):
        status, texto = _request(base, "/cgi-bin/ptz.cgi", {"action": "getPresets", "channel": canal_cgi}, auth)
        print(f"\n--- channel={canal_cgi} -> HTTP {status}")
        print(f"resposta crua ({len(texto)} bytes):")
        for linha in texto.splitlines()[:25]:
            print(f"    {linha}")
        if len(texto.splitlines()) > 25:
            print(f"    ... (+{len(texto.splitlines()) - 25} linhas)")
        if not texto:
            print("    <vazio>")
        parsed = _parse_como_o_sistema_faz(texto)
        print(f"  -> o parser do sistema extrairia: {len(parsed)} preset(s) {parsed[:5]}")

    # Alguns equipamentos so expoem os presets pela arvore de configuracao.
    print("\n" + "=" * 78)
    print("TESTE 2 - configManager (fonte alternativa de presets)")
    print("=" * 78)
    for nome_param in (
        f"PtzPreset[{max(0, args.channel - 1)}]",
        "PtzPreset",
    ):
        status, texto = _request(
            base, "/cgi-bin/configManager.cgi", {"action": "getConfig", "name": nome_param}, auth
        )
        print(f"\n--- getConfig name={nome_param} -> HTTP {status}")
        linhas = [l for l in texto.splitlines() if l.strip()]
        for linha in linhas[:15]:
            print(f"    {linha}")
        if len(linhas) > 15:
            print(f"    ... (+{len(linhas) - 15} linhas)")
        if not linhas:
            print("    <vazio>")

    print("\n" + "=" * 78)
    print("COMO LER ESTE RESULTADO")
    print("=" * 78)
    print(
        "- Resposta crua COM presets e parser extraindo 0  -> o bug esta no parser\n"
        "  (nomes de campo diferentes de Index/Name, ou outro formato).\n"
        "- Resposta crua vazia/Error em 0-based e OK em 1-based -> o bug e a\n"
        "  conversao de canal em get_presets().\n"
        "- Ambos os canais vazios mas configManager listando presets -> a camera\n"
        "  nao expoe por ptz.cgi getPresets; e preciso ler da arvore de config.\n"
        "- Tudo vazio -> a camera realmente nao tem presets salvos nesse canal."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
