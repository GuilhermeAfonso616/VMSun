"""Publish replay manifest clips as fake RTSP cameras through MediaMTX.

This script starts one ffmpeg process per selected clip and publishes each clip
in a loop to the RTSP URL declared in the manifest.

Example:

    python -B scripts/publish_clip_replay_rtsp.py \
        --manifest data/test_replay/clip_replay_manifest.json \
        --max-streams 4 \
        --expectation should_alarm

Then register cameras using the printed rtsp_url values.
"""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST = Path("data/test_replay/clip_replay_manifest.json")
DEFAULT_LOG_DIR = Path("data/test_replay/ffmpeg_logs")


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def select_items(
    items: list[dict[str, Any]],
    *,
    expectation: str,
    replay_ids: set[str],
    max_streams: int | None,
) -> list[dict[str, Any]]:
    selected = []
    for item in items:
        if replay_ids and str(item.get("replay_id") or "") not in replay_ids:
            continue
        if expectation != "all" and str(item.get("expectation") or "") != expectation:
            continue
        if not item.get("clip_path") or not item.get("rtsp_url"):
            continue
        selected.append(item)
        if max_streams is not None and len(selected) >= max_streams:
            break
    return selected


def ffmpeg_command(
    *,
    ffmpeg: str,
    clip_path: Path,
    rtsp_url: str,
    copy_codec: bool,
    fps: float | None,
) -> list[str]:
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-re",
        "-stream_loop",
        "-1",
        "-i",
        str(clip_path),
        "-an",
    ]
    if fps is not None and fps > 0:
        cmd.extend(["-r", str(fps)])
    if copy_codec:
        cmd.extend(["-c:v", "copy"])
    else:
        cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-pix_fmt", "yuv420p"])
    cmd.extend(["-f", "rtsp", "-rtsp_transport", "tcp", rtsp_url])
    return cmd


def stop_processes(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.time() + 5.0
    for process in processes:
        remaining = max(0.1, deadline - time.time())
        try:
            process.wait(timeout=remaining)
        except Exception:
            if process.poll() is None:
                process.kill()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish clip replay manifest entries as RTSP streams.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--expectation", choices=["all", "should_alarm", "should_not_alarm", "unknown", "unknown_existing_alarm"], default="all")
    parser.add_argument("--replay-id", action="append", default=[], help="Specific replay_id to publish. Can be repeated.")
    parser.add_argument("--max-streams", type=int, default=4)
    parser.add_argument("--copy-codec", action="store_true", help="Use -c:v copy instead of transcoding to H.264.")
    parser.add_argument("--fps", type=float, default=None, help="Optional output FPS cap.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg not found in PATH.", file=sys.stderr)
        return 2

    manifest = load_manifest(args.manifest)
    items = select_items(
        list(manifest.get("items") or []),
        expectation=args.expectation,
        replay_ids={str(item) for item in args.replay_id if str(item).strip()},
        max_streams=args.max_streams,
    )
    if not items:
        print("No manifest entries selected.", file=sys.stderr)
        return 1

    args.log_dir.mkdir(parents=True, exist_ok=True)
    processes: list[subprocess.Popen] = []
    log_handles = []

    def handle_stop(signum, frame):  # noqa: ARG001
        print("\nStopping replay publishers...")
        stop_processes(processes)

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    try:
        print("Publishing fake RTSP cameras:")
        for item in items:
            clip_path = Path(str(item["clip_path"]))
            rtsp_url = str(item["rtsp_url"])
            if not clip_path.exists():
                print(f"skip missing clip: {clip_path}", file=sys.stderr)
                continue

            log_path = args.log_dir / f"{item['replay_id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            log_handle = log_path.open("w", encoding="utf-8")
            log_handles.append(log_handle)
            cmd = ffmpeg_command(
                ffmpeg=ffmpeg,
                clip_path=clip_path,
                rtsp_url=rtsp_url,
                copy_codec=bool(args.copy_codec),
                fps=args.fps,
            )
            process = subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT)
            processes.append(process)
            print(
                f"- {item['replay_id']} -> {rtsp_url} "
                f"expectation={item.get('expectation')} label={item.get('feedback_label')}"
            )

        if not processes:
            return 1

        print("\nPress Ctrl+C to stop. Register the RTSP URLs above as test cameras.")
        while any(process.poll() is None for process in processes):
            time.sleep(1.0)

        failed = [process.returncode for process in processes if process.returncode not in (0, None)]
        return 1 if failed else 0
    finally:
        stop_processes(processes)
        for handle in log_handles:
            handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
