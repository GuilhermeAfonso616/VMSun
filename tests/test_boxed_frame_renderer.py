import cv2
import numpy as np

from app.services.boxed_frame_renderer import map_bbox_to_frame, render_tracks_on_jpeg


def test_map_bbox_accounts_for_portrait_letterbox():
    mapped = map_bbox_to_frame(
        [0.0, 312.0, 1152.0, 1882.0],
        source_width=1152,
        source_height=1920,
        frame_width=960,
        frame_height=540,
    )

    assert mapped == (318, 88, 642, 529)


def test_render_tracks_changes_pixels_at_mapped_box():
    frame = np.zeros((540, 960, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", frame)
    assert ok

    result = render_tracks_on_jpeg(
        encoded.tobytes(),
        {
            "source_frame_width": 1152,
            "source_frame_height": 1920,
            "tracks": [
                {
                    "bbox": [0.0, 312.0, 1152.0, 1882.0],
                    "track_id": 119,
                    "confidence": 0.80,
                }
            ],
        },
    )

    rendered = cv2.imdecode(np.frombuffer(result, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert rendered is not None
    assert int(rendered[88:94, 318:642].sum()) > 0
