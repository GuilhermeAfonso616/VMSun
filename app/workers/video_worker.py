from app.core.config import settings
from app.runtime import BaseCameraWorker, CameraRuntimeConfig, NormalInferenceScheduler


class VideoWorker(BaseCameraWorker):
    mode_name = "normal"

    def build_scheduler(self, runtime_config: CameraRuntimeConfig):
        return NormalInferenceScheduler(
            inference_interval=settings.normal_inference_interval_seconds
        )