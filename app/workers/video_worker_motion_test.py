from app.runtime import BaseCameraWorker, CameraRuntimeConfig, MotionAwareInferenceScheduler
from app.core.config import settings


class VideoWorkerMotionTest(BaseCameraWorker):
    mode_name = "motion_test"

    def build_scheduler(self, runtime_config: CameraRuntimeConfig):
        return MotionAwareInferenceScheduler(runtime_config.motion)

    def render_frame(self, annotated_frame, geometry, infer_ran: bool, decision, roi_crop_meta, tracks, visual_tracks=None):
        if not bool(settings.visual_motion_debug_overlay_enabled):
            return

        super().render_frame(annotated_frame, geometry, infer_ran, decision, roi_crop_meta, tracks, visual_tracks)
        motion_info = decision.motion_info or {}
        self.renderer.draw_motion_boxes(
            annotated_frame,
            motion_info.get("moving_boxes", []),
            roi_meta=roi_crop_meta,
            downscale_width=self.scheduler.gate.downscale_width,
        )
        self.renderer.draw_motion_indicator(
            annotated_frame,
            bool(motion_info.get("motion_detected", False)),
        )
        self.renderer.draw_motion_status_panel(
            annotated_frame,
            motion_info,
            should_infer=decision.should_infer,
            infer_ran=infer_ran,
            tracks_count=len(tracks),
        )
