"""Stub de compatibilidade para track_store no VMSun."""

class TrackStore:
    def get_tracks(self, *args, **kwargs) -> list:
        return []

    def get_track(self, *args, **kwargs) -> None:
        return None

    def update_track(self, *args, **kwargs) -> None:
        pass

    def remove_track(self, *args, **kwargs) -> None:
        pass


track_store = TrackStore()
