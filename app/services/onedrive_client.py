"""Stub de compatibilidade para onedrive_client no VMSun."""

class DummyOneDriveClient:
    def enabled(self) -> bool:
        return False

    def upload_operator_performance_log(self, *args, **kwargs) -> None:
        return None

    def upload_clip(self, *args, **kwargs) -> None:
        return None


onedrive_client = DummyOneDriveClient()
