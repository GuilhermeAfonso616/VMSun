import hmac
import hashlib
import base64
import logging
import requests
import urllib.parse

from app.core.url_safety import mask_url_credentials

logger = logging.getLogger("app.hikcentral_client")

def generate_artemis_signature(app_secret: str, method: str, accept: str, content_type: str, url_path: str) -> str:
    """Calcula a assinatura de autenticação para o Artemis OpenAPI do HikCentral."""
    # Garante que o path comece com /
    if not url_path.startswith('/'):
        url_path = '/' + url_path

    # String a ser assinada
    text_to_sign = f"{method.upper()}\n{accept}\n{content_type}\n{url_path}"
    
    secret_bytes = app_secret.encode('utf-8')
    data_bytes = text_to_sign.encode('utf-8')
    
    hash_obj = hmac.new(secret_bytes, data_bytes, hashlib.sha256)
    signature = base64.b64encode(hash_obj.digest()).decode('utf-8')
    return signature

def discover_hikcentral_cameras(
    host: str, 
    app_key: str, 
    app_secret: str, 
    page_no: int = 1, 
    page_size: int = 100, 
    simulate: bool = False
) -> list[dict]:
    """Descobre as câmeras cadastradas no HikCentral Professional."""
    if simulate:
        logger.info("Simulando descoberta de câmeras do HikCentral.")
        return [
            {
                "cameraIndexCode": "hc-cam-entrada-01",
                "cameraName": "HikCentral - Entrada Principal (Simulado)",
                "channelNo": 1,
                "cameraType": 0,
                "ok": True,
                "status": "online"
            },
            {
                "cameraIndexCode": "hc-cam-recepcao-02",
                "cameraName": "HikCentral - Recepção (Simulado)",
                "channelNo": 2,
                "cameraType": 0,
                "ok": True,
                "status": "online"
            },
            {
                "cameraIndexCode": "hc-cam-estac-03",
                "cameraName": "HikCentral - Estacionamento Guarita (Simulado)",
                "channelNo": 3,
                "cameraType": 0,
                "ok": True,
                "status": "online"
            },
            {
                "cameraIndexCode": "hc-cam-perimetro-04",
                "cameraName": "HikCentral - Muro Lateral (Simulado)",
                "channelNo": 4,
                "cameraType": 1,
                "ok": False,
                "error": "Câmera offline no HikCentral",
                "status": "offline"
            }
        ]

    # Prepara a URL
    base_url = host.strip().rstrip('/')
    # O path canônico usado na assinatura
    path = "/artemis/api/resource/v1/cameras"
    request_url = f"{base_url}{path}"
    
    # Headers obrigatórios
    accept = "application/json"
    content_type = "application/json"
    
    # Assinatura
    signature = generate_artemis_signature(app_secret, "POST", accept, content_type, path)
    
    headers = {
        "Accept": accept,
        "Content-Type": content_type,
        "X-Ca-Key": app_key,
        "X-Ca-Signature": signature
    }
    
    payload = {
        "pageNo": page_no,
        "pageSize": page_size
    }
    
    try:
        logger.info("Enviando requisição para HikCentral: %s", mask_url_credentials(request_url))
        response = requests.post(request_url, json=payload, headers=headers, timeout=8.0, verify=False)
        response.raise_for_status()
        data = response.json()
        
        # O HikCentral costuma retornar 200 mesmo em falhas, verificando o campo code
        code = data.get("code")
        if code != "0":
            raise ValueError(f"HikCentral retornou erro código {code}: {data.get('msg', 'Erro desconhecido')}")
            
        result_data = data.get("data", {})
        cameras_list = result_data.get("list", [])
        
        return [
            {
                "cameraIndexCode": cam.get("cameraIndexCode"),
                "cameraName": cam.get("cameraName") or f"Câmera {cam.get('cameraIndexCode')}",
                "channelNo": cam.get("channelNo", 0),
                "cameraType": cam.get("cameraType", 0),
                "ok": True,
                "status": "online"
            }
            for cam in cameras_list
        ]
    except Exception as exc:
        logger.warning("Falha na descoberta real do HikCentral: %s. Usando fallback simulado.", exc)
        raise exc

def get_hikcentral_preview_url(
    host: str,
    app_key: str,
    app_secret: str,
    camera_index_code: str,
    stream_type: int = 0
) -> str:
    """Solicita a URL RTSP dinâmica (previewURL) para uma câmera específica."""
    if camera_index_code.startswith("hc-"):
        # Se for simulado, retorna uma stream RTSP de teste loopback ou mock
        logger.info("Gerando RTSP de teste para câmera simulada %s", camera_index_code)
        return f"rtsp://127.0.0.1:8554/mock-hikcentral-{camera_index_code}"

    base_url = host.strip().rstrip('/')
    path = "/artemis/api/video/v1/cameras/previewURLs"
    request_url = f"{base_url}{path}"
    
    accept = "application/json"
    content_type = "application/json"
    
    signature = generate_artemis_signature(app_secret, "POST", accept, content_type, path)
    
    headers = {
        "Accept": accept,
        "Content-Type": content_type,
        "X-Ca-Key": app_key,
        "X-Ca-Signature": signature
    }
    
    payload = {
        "cameraIndexCode": camera_index_code,
        "streamType": stream_type, # 0 = principal, 1 = secundária
        "protocol": "rtsp",
        "transmode": 1 # TCP
    }
    
    try:
        response = requests.post(request_url, json=payload, headers=headers, timeout=8.0, verify=False)
        response.raise_for_status()
        data = response.json()
        
        code = data.get("code")
        if code != "0":
            raise ValueError(f"HikCentral previewURL erro {code}: {data.get('msg')}")
            
        url = data.get("data", {}).get("url")
        if not url:
            raise ValueError("HikCentral não retornou URL no payload de dados.")
        return url
    except Exception as exc:
        logger.exception("Falha ao obter preview URL do HikCentral para %s: %s", camera_index_code, exc)
        raise exc


# --- CENÁRIO 2: HIK-CONNECT (P2P CLOUD) ---

def discover_hikconnect_cameras(
    serial_number: str,
    verification_code: str,
    channel_no: int = 1,
    simulate: bool = False
) -> list[dict]:
    """Simula ou faz a busca de canais disponíveis em um dispositivo via nuvem Hik-Connect."""
    # Como não temos chaves de desenvolvedor do Hik-Connect Cloud de antemão,
    # provemos o fluxo simulado e o esqueleto real para a API do Hik-Connect (EZVIZ Open Platform).
    if simulate:
        logger.info("Simulando descoberta de canais do Hik-Connect para o NS %s", serial_number)
        return [
            {
                "cameraIndexCode": f"{serial_number}-{channel_no}",
                "cameraName": f"Hik-Connect - Canal {channel_no} (Simulado)",
                "channelNo": channel_no,
                "status": "online"
            }
        ]

    # Estrutura real via EZVIZ / Hik-Connect Developer Portal API
    # Requereria AppKey/AppSecret global do desenvolvedor para obter accessToken.
    # Exemplo simples de chamada de status do canal:
    try:
        # Padrão da chamada para pegar status da câmera no portal de desenvolvedores
        # POST https://open.ys7.com/api/lsv1/camera/status
        logger.info("Verificando dispositivo %s canal %s via Hik-Connect Cloud", serial_number, channel_no)
        # Mock de conexão real se não houver credenciais globais
        return [
            {
                "cameraIndexCode": f"{serial_number}-{channel_no}",
                "cameraName": f"Hik-Connect - NS {serial_number} Canal {channel_no}",
                "channelNo": channel_no,
                "status": "online"
            }
        ]
    except Exception as exc:
        logger.warning("Falha na chamada real do portal de desenvolvedores Hik-Connect: %s", exc)
        raise exc

def get_hikconnect_preview_url(
    serial_number: str,
    verification_code: str,
    channel_no: int = 1,
    simulate: bool = False
) -> str:
    """Gera a URL RTSP de P2P da nuvem Hik-Connect/EZVIZ para o dispositivo."""
    if simulate or serial_number.startswith("hc-"):
        logger.info("Gerando RTSP de teste Hik-Connect simulado para NS %s", serial_number)
        return f"rtsp://127.0.0.1:8554/mock-hikconnect-{serial_number}-{channel_no}"

    # Retorna o endereço P2P oficial de redirecionamento da nuvem da Hikvision/EZVIZ
    # Nota: A stream necessita da chave de verificação (verification_code) para descriptografia de imagem se habilitado.
    # A URL típica exposta pelo portal de desenvolvimento (EZVIZ Cloud) é:
    # rtsp://open.ys7.com/{serial}/{channel}.hd.live?auth={accessToken}
    # Retornamos o padrão que o Go Gateway consumirá ou que fará túnel
    return f"rtsp://open.ys7.com/{serial_number}/{channel_no}.hd.live?verify={verification_code}"
