import copy
import os
from threading import Lock

from ultralytics import YOLO

from app.core.config import settings
from app.core.logging import get_logger


class PersonDetector:
    # Os pesos sao carregados UMA unica vez e compartilhados entre todas as
    # cameras (uma copia do modelo na GPU). O estado do TRACKER, porem, e
    # isolado por camera atraves de um predictor proprio (ver _build_isolated_model).
    _shared_model = None
    _model_path = None
    _backend = "pytorch"
    _lock = Lock()

    def __init__(self, *, force_pytorch: bool = False):
        self.logger = get_logger("app.detector")
        self.force_pytorch = bool(force_pytorch)
        self.device = settings.resolved_detect_device()
        self.use_half = bool(settings.detector_fp16_enabled) and self.device.startswith("cuda")
        self.model = self._build_isolated_model()
        self.logger.info(
            "Detector inicializado model=%s backend=%s device=%s half=%s isolated=%s",
            type(self)._model_path,
            type(self)._backend,
            self.device,
            self.use_half,
            self.model is not None and self.model is not type(self)._shared_model,
        )

    @staticmethod
    def _resolve_model_path(*, force_pytorch: bool = False) -> tuple[str, str]:
        """Decide entre engine TensorRT/ONNX e o .pt, com fallback seguro.

        Retorna (caminho, backend). Se detector_engine_path estiver setado e o
        arquivo existir, usa-o; caso contrario cai para o .pt. Assim, ligar o
        TensorRT e so apontar a env e gerar o .engine — sem risco de derrubar o
        servico se o arquivo nao estiver presente naquela maquina.
        """
        if force_pytorch:
            return settings.detector_model_path, "pytorch"
        engine_path = str(getattr(settings, "detector_engine_path", "") or "").strip()
        if engine_path and os.path.exists(engine_path):
            suffix = os.path.splitext(engine_path)[1].lower()
            backend = "tensorrt" if suffix == ".engine" else "onnx" if suffix == ".onnx" else "engine"
            return engine_path, backend
        return settings.detector_model_path, "pytorch"

    @classmethod
    def reset_shared_model(cls) -> None:
        with cls._lock:
            cls._shared_model = None
            cls._model_path = None
            cls._backend = "pytorch"

    @classmethod
    def _get_shared_model(cls, *, force_pytorch: bool = False):
        model_path, backend = cls._resolve_model_path(force_pytorch=force_pytorch)

        with cls._lock:
            if cls._shared_model is None or cls._model_path != model_path:
                cls._shared_model = YOLO(model_path)
                cls._model_path = model_path
                cls._backend = backend
            return cls._shared_model

    def _build_isolated_model(self):
        """Retorna um wrapper YOLO com predictor/tracker proprios desta camera.

        Antes, todas as cameras chamavam track(persist=True) sobre o MESMO
        objeto YOLO, fazendo o estado do tracker (track_ids, historico) vazar
        entre cameras e crescer em memoria sem isolamento. Aqui fazemos um
        shallow copy que reutiliza os pesos (.model / nn.Module) mas zera o
        predictor, forcando o ultralytics a criar um tracker exclusivo no
        primeiro track(). Memoria de GPU permanece compartilhada.
        """
        shared = self._get_shared_model(force_pytorch=self.force_pytorch)
        try:
            isolated = copy.copy(shared)
            # Forca a criacao de um predictor (e tracker) novo e exclusivo
            # desta camera no primeiro track().
            isolated.predictor = None
            return isolated
        except Exception:
            # Fallback seguro: se a versao do ultralytics nao suportar o clone,
            # mantem o comportamento anterior (modelo compartilhado) em vez de
            # derrubar o worker. O isolamento e uma otimizacao, nao requisito.
            self.logger.exception(
                "Falha ao isolar predictor por camera; usando modelo compartilhado."
            )
            return shared

    def track(self, frame):
        # half so se aplica ao backend PyTorch; no TensorRT/ONNX a precisao ja
        # vem embutida no engine, e passar half=True gera warning a cada frame.
        kwargs = dict(
            source=frame,
            classes=[0],
            conf=settings.detect_conf,
            imgsz=settings.detect_imgsz,
            device=self.device,
            verbose=False,
            persist=True,
            tracker=settings.tracker_config,
        )
        if type(self)._backend == "pytorch":
            kwargs["half"] = bool(settings.detector_fp16_enabled) and self.device.startswith("cuda")
        return self.model.track(**kwargs)
