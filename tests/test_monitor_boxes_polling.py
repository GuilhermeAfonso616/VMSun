from pathlib import Path
import unittest


class MonitorBoxesPollingTest(unittest.TestCase):
    def test_monitor_web_does_not_disable_boxes_with_hardcoded_motion_mode(self):
        monitor_script = Path("app/static/js/monitor_vms.js").read_text(encoding="utf-8")

        self.assertNotIn('function cameraModeValue(camera) {\n        return "motion";', monitor_script)
        self.assertNotIn(
            'cameraModeValue(camera) !== "motion" && cameraCanRenderBoxes(camera)',
            monitor_script,
        )
        self.assertIn('return cameraCanRenderBoxes(camera);', monitor_script)

    def test_canary_sse_coalesces_and_rejects_stale_identity(self):
        monitor_script = Path("app/static/js/monitor_vms.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('webTrackTransportMode === "sse_prefer"', monitor_script)
        self.assertIn('params.set("interval_ms", "25")', monitor_script)
        self.assertIn("pendingTrackUpdates.set(key, payload)", monitor_script)
        self.assertIn("window.requestAnimationFrame(function ()", monitor_script)
        self.assertIn(
            "Number(camera.monitor_boxes_frame_id || 0) >= incomingFrameId",
            monitor_script,
        )
        self.assertIn(
            "camera.monitor_boxes_generation_id === incomingGenerationId",
            monitor_script,
        )
        self.assertIn("visual_empty_results_total += 1", monitor_script)
        self.assertIn("monitor_boxes = []", monitor_script)
        self.assertIn("client_render_ms", monitor_script)

    def test_operator_app_uses_callback_renderer_for_video_with_track_overlay(self):
        view_model = Path(
            "operator-client/src/Analitico.Operator.App/ViewModels/CameraTileViewModel.cs"
        ).read_text(encoding="utf-8")
        slot_view_model = Path(
            "operator-client/src/Analitico.Operator.App/ViewModels/CameraSlotViewModel.cs"
        ).read_text(encoding="utf-8")
        main_window = Path(
            "operator-client/src/Analitico.Operator.App/MainWindow.axaml"
        ).read_text(encoding="utf-8")

        self.assertIn("public bool UsesBoxedPlayback => false;", view_model)
        self.assertIn("public bool ShowTrackOverlay => BoxesEnabled;", view_model)
        self.assertIn("VideoAspectRatio", view_model)
        self.assertIn("player.AspectRatio", slot_view_model)
        self.assertIn("controls:NativeDragVideoView", main_window)
        self.assertIn("controls:CallbackTrackVideoView", main_window)
        self.assertIn('IsActive="{Binding UseCallbackPlayback}"', main_window)
        self.assertNotIn("controls:MjpegVideoView", main_window)
        self.assertNotIn("ClientOverlayStreamUrl", view_model)


if __name__ == "__main__":
    unittest.main()
