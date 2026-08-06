from datetime import datetime

from app.services import metrics_store as metrics_store_module


def test_set_metrics_adds_timestamp_and_persists_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(metrics_store_module.settings, "runtime_state_dir", str(tmp_path))
    store = metrics_store_module.MetricsStore()

    saved = store.set_metrics(37, {"raw_fps": 15.0})

    assert saved["raw_fps"] == 15.0
    assert datetime.fromisoformat(saved["updated_at"])
    assert store.get_metrics(37) == saved
