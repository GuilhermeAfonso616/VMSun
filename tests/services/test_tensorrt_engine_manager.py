from __future__ import annotations

import os

from app.core.config import settings
from app.services import tensorrt_engine_manager as manager


def _restore_settings(original: dict):
    for key, value in original.items():
        setattr(settings, key, value)


def test_auto_build_disabled_uses_pytorch(monkeypatch, tmp_path):
    original = {
        "detector_engine_path": settings.detector_engine_path,
        "detector_engine_auto_build_enabled": settings.detector_engine_auto_build_enabled,
        "detector_engine_auto_build_required": settings.detector_engine_auto_build_required,
        "detector_model_path": settings.detector_model_path,
    }
    monkeypatch.delenv("DETECTOR_ENGINE_PATH", raising=False)
    try:
        model = tmp_path / "detector.pt"
        model.write_bytes(b"weights")
        settings.detector_model_path = str(model)
        settings.detector_engine_path = ""
        settings.detector_engine_auto_build_enabled = False
        settings.detector_engine_auto_build_required = False
        manager._LAST_ENGINE_SNAPSHOT = {}

        snapshot = manager.ensure_detector_tensorrt_engine()

        assert snapshot["status"] == "pytorch"
        assert snapshot["reason"] == "auto_build_disabled"
        assert "DETECTOR_ENGINE_PATH" not in os.environ
    finally:
        manager._LAST_ENGINE_SNAPSHOT = {}
        _restore_settings(original)


def test_existing_configured_engine_is_selected(monkeypatch, tmp_path):
    original = {
        "detector_engine_path": settings.detector_engine_path,
        "detector_engine_auto_build_enabled": settings.detector_engine_auto_build_enabled,
        "detector_engine_auto_build_required": settings.detector_engine_auto_build_required,
        "detector_model_path": settings.detector_model_path,
    }
    previous_env = os.environ.get("DETECTOR_ENGINE_PATH")
    try:
        model = tmp_path / "detector.pt"
        engine = tmp_path / "detector.engine"
        model.write_bytes(b"weights")
        engine.write_bytes(b"engine")
        settings.detector_model_path = str(model)
        settings.detector_engine_path = str(engine)
        settings.detector_engine_auto_build_enabled = True
        settings.detector_engine_auto_build_required = False
        manager._LAST_ENGINE_SNAPSHOT = {}

        snapshot = manager.ensure_detector_tensorrt_engine()

        assert snapshot["status"] == "ready"
        assert snapshot["reason"] == "configured_engine_exists"
        assert snapshot["backend"] == "tensorrt"
        assert os.environ["DETECTOR_ENGINE_PATH"] == str(engine.resolve())
    finally:
        manager._LAST_ENGINE_SNAPSHOT = {}
        if previous_env is None:
            monkeypatch.delenv("DETECTOR_ENGINE_PATH", raising=False)
        else:
            monkeypatch.setenv("DETECTOR_ENGINE_PATH", previous_env)
        _restore_settings(original)


def test_engine_status_snapshot_reports_no_engine(monkeypatch):
    original = {
        "detector_engine_path": settings.detector_engine_path,
        "detector_engine_auto_build_enabled": settings.detector_engine_auto_build_enabled,
        "detector_engine_auto_build_required": settings.detector_engine_auto_build_required,
    }
    try:
        settings.detector_engine_path = ""
        settings.detector_engine_auto_build_enabled = False
        settings.detector_engine_auto_build_required = False
        manager._LAST_ENGINE_SNAPSHOT = {}

        snapshot = manager.engine_status_snapshot()

        assert snapshot["status"] == "pytorch"
        assert snapshot["reason"] == "no_engine_configured"
    finally:
        manager._LAST_ENGINE_SNAPSHOT = {}
        _restore_settings(original)
