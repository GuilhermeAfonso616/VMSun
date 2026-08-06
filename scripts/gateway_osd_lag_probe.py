"""Collect time-aligned MediaMTX/Gateway pairs for a camera OSD.

The probe keeps one RTSP decoder warm, samples the latest JPEG published by the
Gateway and aligns it with MediaMTX by host receive time. It does not persist
images unless ``--montage-output`` is explicitly supplied for controlled visual
QA. The OSD must be read from that montage; receipt alignment is not source age.
"""

from __future__ import annotations

import argparse
import base64
import collections
import json
import statistics
import subprocess
import threading
import time
import urllib.request
from datetime import datetime

import cv2
import numpy as np


def parse_rfc3339_ns(value: str) -> int:
    text = str(value).strip().replace("Z", "+00:00")
    fraction = text.find(".")
    timezone_mark = max(text.rfind("+"), text.rfind("-"))
    if fraction >= 0 and timezone_mark > fraction:
        digits = text[fraction + 1 : timezone_mark]
        if len(digits) > 6:
            text = text[: fraction + 1] + digits[:6] + text[timezone_mark:]
    return int(datetime.fromisoformat(text).timestamp() * 1_000_000_000)


def decode_jpeg(payload: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("invalid JPEG")
    return image


def osd_signature(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    crop = image[0 : max(24, int(height * 0.09)), int(width * 0.50) : int(width * 0.99)]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (640, 72), interpolation=cv2.INTER_AREA)
    _, bright = cv2.threshold(gray, 165, 255, cv2.THRESH_BINARY)
    return cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8))


def signature_distance(left: np.ndarray, right: np.ndarray) -> float:
    union = cv2.bitwise_or(left, right)
    mask = union > 0
    if not np.any(mask):
        return float("inf")
    difference = cv2.absdiff(left, right)
    return float(np.mean(difference[mask]))


class MediaMTXBuffer:
    def __init__(self, source: str, ffmpeg: str = "ffmpeg"):
        self.frames: collections.deque[tuple[int, np.ndarray, np.ndarray]] = collections.deque(maxlen=600)
        self._stop = threading.Event()
        self.command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-fflags",
            "+nobuffer+discardcorrupt",
            "-flags",
            "low_delay",
            "-max_delay",
            "0",
            "-analyzeduration",
            "500000",
            "-probesize",
            "500000",
            "-i",
            source,
            "-vf",
            "fps=5",
            "-q:v",
            "4",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        self.process: subprocess.Popen | None = None
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        while not self._stop.is_set():
            buffer = bytearray()
            self.process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert self.process.stdout is not None
            while not self._stop.is_set():
                chunk = self.process.stdout.read(32 * 1024)
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b"\xff\xd8")
                    end = buffer.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                    if start < 0 or end < 0:
                        break
                    raw = bytes(buffer[start : end + 2])
                    del buffer[: end + 2]
                    try:
                        image = decode_jpeg(raw)
                        self.frames.append((time.time_ns(), osd_signature(image), image))
                    except RuntimeError:
                        continue
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            if not self._stop.wait(1.0):
                continue

    def close(self) -> None:
        self._stop.set()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=8) as response:
        return json.load(response)


def latest_gateway_frame(base_url: str, camera_id: int) -> tuple[int, np.ndarray, np.ndarray]:
    endpoint = f"{base_url.rstrip('/')}/cameras/{camera_id}/frames"
    first = fetch_json(f"{endpoint}?after_seq=0&limit=5")
    latest_seq = int(first.get("latest_seq") or 0)
    payload = fetch_json(f"{endpoint}?after_seq={max(0, latest_seq - 1)}&limit=1")
    frames = payload.get("frames") or []
    if not frames:
        raise RuntimeError("Gateway did not return a latest frame")
    item = frames[-1]
    captured_at_ns = parse_rfc3339_ns(item["captured_at"])
    image = decode_jpeg(base64.b64decode(item["jpeg_base64"]))
    return captured_at_ns, osd_signature(image), image


def labelled_pair(media_image: np.ndarray, gateway_image: np.ndarray, sample: int) -> np.ndarray:
    panels = []
    for label, image in (("MediaMTX", media_image), ("Gateway", gateway_image)):
        height = 288
        resized = cv2.resize(
            image,
            (round(image.shape[1] * height / image.shape[0]), height),
            interpolation=cv2.INTER_AREA,
        )
        cv2.rectangle(resized, (0, 0), (230, 30), (0, 0, 0), -1)
        cv2.putText(
            resized,
            f"{label} #{sample}",
            (6, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        panels.append(resized)
    return cv2.hconcat(panels)


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * ratio)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-id", type=int, required=True)
    parser.add_argument("--media-source", required=True)
    parser.add_argument("--gateway-url", default="http://localhost:8090")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--interval-seconds", type=float, default=12.0)
    parser.add_argument("--warmup-seconds", type=float, default=8.0)
    parser.add_argument("--montage-output")
    args = parser.parse_args()

    stream = MediaMTXBuffer(args.media_source)
    samples: list[dict] = []
    montage_rows: list[np.ndarray] = []
    try:
        time.sleep(max(1.0, args.warmup_seconds))
        for index in range(max(1, args.samples)):
            captured_at_ns, signature, gateway_image = latest_gateway_frame(
                args.gateway_url,
                args.camera_id,
            )
            candidates = list(stream.frames)
            if not candidates:
                raise RuntimeError("No MediaMTX frame available")
            received_at_ns, candidate, media_image = min(
                candidates,
                key=lambda item: abs(item[0] - captured_at_ns),
            )
            if abs(received_at_ns - captured_at_ns) > 1_000_000_000:
                raise RuntimeError("MediaMTX reference frame is stale")
            score = signature_distance(signature, candidate)
            samples.append(
                {
                    "sample": index + 1,
                    "capture_alignment_ms": round(
                        (captured_at_ns - received_at_ns) / 1_000_000,
                        3,
                    ),
                    "match_score": round(score, 3),
                    "candidate_count": len(candidates),
                }
            )
            if args.montage_output:
                montage_rows.append(labelled_pair(media_image, gateway_image, index + 1))
            if index + 1 < args.samples:
                time.sleep(max(0.1, args.interval_seconds))
    finally:
        stream.close()

    if args.montage_output and montage_rows:
        cv2.imwrite(args.montage_output, cv2.vconcat(montage_rows))

    alignment_values = [abs(float(item["capture_alignment_ms"])) for item in samples]
    result = {
        "camera_id": args.camera_id,
        "samples": samples,
        "capture_alignment_summary": {
            "n": len(alignment_values),
            "mean_abs_ms": round(statistics.fmean(alignment_values), 3),
            "p50_abs_ms": round(percentile(alignment_values, 0.50), 3),
            "p95_abs_ms": round(percentile(alignment_values, 0.95), 3),
            "max_abs_ms": round(max(alignment_values), 3),
        },
        "osd_lag_ms": None,
        "limitations": [
            "OSD lag requires visual reading of the optional time-aligned montage",
            "OSD resolution usually bounds precision to one second",
            "Capture alignment is not source capture age",
        ],
    }
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
