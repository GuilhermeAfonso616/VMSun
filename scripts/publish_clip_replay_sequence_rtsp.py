"""Publish one fake RTSP camera with clips separated by quiet still frames.

This runner is useful for long tests where alerts should not fire continuously.
It publishes a single RTSP path as one continuous ffmpeg stream, plays clips in
sequence, inserts a static quiet image between clips, then repeats the sequence.

Example:

    python -B scripts/publish_clip_replay_sequence_rtsp.py \
        --manifest data/test_replay/clip_replay_manifest.json \
        --rtsp-url rtsp://localhost:8554/replay_mixed_01 \
        --pause-seconds 30 \
        --mode mixed \
        --chunk-size 8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST = Path("data/test_replay/clip_replay_manifest.json")
DEFAULT_RTSP_URL = "rtsp://localhost:8554/replay_sequence_01"
DEFAULT_LOG_DIR = Path("data/test_replay/ffmpeg_sequence_logs")
DEFAULT_CHUNK_SIZE = 8
DEFAULT_SEGMENT_CACHE_DIR = Path("data/test_replay/normalized_sequence_segments")
DEFAULT_PAUSE_LINES = (
    "PAUSA ENTRE TESTES",
    "SEM MOVIMENTO / SEM ALERTA",
    "AGUARDANDO PROXIMO CLIP",
)


FONT_5X7 = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _draw_rect(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    rect_width: int,
    rect_height: int,
    color: tuple[int, int, int],
) -> None:
    for yy in range(max(0, y), min(height, y + rect_height)):
        row_offset = yy * width * 3
        for xx in range(max(0, x), min(width, x + rect_width)):
            offset = row_offset + xx * 3
            pixels[offset : offset + 3] = bytes(color)


def _text_width(text: str, scale: int) -> int:
    return max(0, len(text) * 6 * scale - scale)


def _draw_text(
    pixels: bytearray,
    width: int,
    height: int,
    text: str,
    x: int,
    y: int,
    scale: int,
    color: tuple[int, int, int],
) -> None:
    cursor_x = x
    for char in text.upper():
        glyph = FONT_5X7.get(char, FONT_5X7[" "])
        for row_index, row in enumerate(glyph):
            for col_index, enabled in enumerate(row):
                if enabled == "1":
                    _draw_rect(
                        pixels,
                        width,
                        height,
                        cursor_x + col_index * scale,
                        y + row_index * scale,
                        scale,
                        scale,
                        color,
                    )
        cursor_x += 6 * scale


def pause_image_path(width: int, height: int, lines: list[str]) -> Path:
    tmp_dir = Path(tempfile.gettempdir()) / "analitico_clip_replay"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    text_hash = hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:10]
    path = tmp_dir / f"pause_{width}x{height}_{text_hash}.ppm"
    if path.exists():
        return path

    width = int(width)
    height = int(height)
    bg = bytes([8, 12, 18])
    pixels = bytearray(bg * width * height)

    accent = (38, 86, 220)
    muted = (102, 122, 148)
    primary = (238, 246, 255)
    secondary = (178, 194, 214)

    _draw_rect(pixels, width, height, 0, 0, width, max(10, height // 70), accent)
    _draw_rect(pixels, width, height, 0, height - max(6, height // 100), width, max(6, height // 100), muted)

    clean_lines = [line.strip().upper() for line in lines if line and line.strip()]
    if not clean_lines:
        clean_lines = list(DEFAULT_PAUSE_LINES)
    max_len = max(len(line) for line in clean_lines)
    scale_by_width = int((width * 0.82) / max(1, max_len * 6))
    scale_by_height = int((height * 0.34) / max(1, len(clean_lines) * 9))
    scale = max(3, min(scale_by_width, scale_by_height))
    total_height = len(clean_lines) * 8 * scale
    start_y = max(height // 4, (height - total_height) // 2)

    for index, line in enumerate(clean_lines):
        color = primary if index == 0 else secondary
        line_scale = scale if index == 0 else max(3, int(scale * 0.68))
        line_width = _text_width(line, line_scale)
        line_x = max(0, (width - line_width) // 2)
        line_y = start_y + index * 9 * scale
        _draw_text(pixels, width, height, line, line_x, line_y, line_scale, color)

    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    path.write_bytes(header + pixels)
    return path


def run_ffmpeg_segment(cmd: list[str], log_path: Path, stop_requested: callable) -> int:
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n\n=== " + datetime.now().isoformat(timespec="seconds") + " ===\n")
        log.write(" ".join(cmd) + "\n")
        log.flush()
        process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        while process.poll() is None:
            if stop_requested():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
                return 130
            time.sleep(0.25)
        return int(process.returncode or 0)


def iter_chunks(items: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    if chunk_size <= 0:
        return [items]
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def _path_fingerprint(path: Path) -> str:
    try:
        stat = path.stat()
        return f"{path.resolve()}:{stat.st_size}:{int(stat.st_mtime)}"
    except Exception:
        return str(path)


def _segment_cache_key(items: list[dict[str, Any]], width: int, height: int, fps: float, pause_seconds: float, pause_image: Path) -> str:
    payload = {
        "clips": [_path_fingerprint(Path(str(item["clip_path"]))) for item in items],
        "width": int(width),
        "height": int(height),
        "fps": float(fps),
        "pause_seconds": float(pause_seconds),
        "pause_image": _path_fingerprint(pause_image),
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _ffmpeg_scale_filter(width: int, height: int, fps: float) -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={max(1.0, float(fps))},setsar=1,format=yuv420p"
    )


def _run_prepare_command(cmd: list[str], log_path: Path) -> None:
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n\n=== prepare " + datetime.now().isoformat(timespec="seconds") + " ===\n")
        log.write(" ".join(cmd) + "\n")
        log.flush()
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)


def _concat_file_line(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("'", "\\'")
    return f"file '{escaped}'\n"


def _has_valid_segment(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 0
    except Exception:
        return False


def prepare_playlist_segments(
    *,
    ffmpeg: str,
    items: list[dict[str, Any]],
    width: int,
    height: int,
    fps: float,
    pause_seconds: float,
    pause_image: Path,
    cache_dir: Path,
    log_path: Path,
) -> Path:
    cache_key = _segment_cache_key(items, width, height, fps, pause_seconds, pause_image)
    work_dir = cache_dir / cache_key
    work_dir.mkdir(parents=True, exist_ok=True)
    playlist_path = work_dir / "sequence.ffconcat"
    scale_filter = _ffmpeg_scale_filter(width, height, fps)
    segment_paths: list[Path] = []

    pause_segment: Path | None = None
    if pause_seconds > 0:
        pause_segment = work_dir / f"pause_{int(max(1.0, pause_seconds) * 1000)}ms.mp4"
        if not _has_valid_segment(pause_segment):
            _run_prepare_command(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-nostdin",
                    "-y",
                    "-loop",
                    "1",
                    "-t",
                    str(max(0.1, pause_seconds)),
                    "-i",
                    str(pause_image),
                    "-an",
                    "-vf",
                    scale_filter,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-tune",
                    "stillimage",
                    "-g",
                    str(max(10, int(round(float(fps) * 2)))),
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(pause_segment),
                ],
                log_path,
            )

    for index, item in enumerate(items):
        clip_path = Path(str(item["clip_path"]))
        segment_path = work_dir / f"clip_{index:04d}_{hashlib.sha1(str(clip_path).encode('utf-8')).hexdigest()[:10]}.mp4"
        if not _has_valid_segment(segment_path):
            print(f"[prepare] normalizing {index + 1}/{len(items)} {clip_path.name}")
            _run_prepare_command(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(clip_path),
                    "-an",
                    "-vf",
                    scale_filter,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-g",
                    str(max(10, int(round(float(fps) * 2)))),
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(segment_path),
                ],
                log_path,
            )
        segment_paths.append(segment_path)

    with playlist_path.open("w", encoding="utf-8") as playlist:
        playlist.write("ffconcat version 1.0\n")
        for segment_path in segment_paths:
            playlist.write(_concat_file_line(segment_path))
            if pause_segment is not None:
                playlist.write(_concat_file_line(pause_segment))

    return playlist_path


def playlist_command(*, ffmpeg: str, playlist_path: Path, rtsp_url: str) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-re",
        "-stream_loop",
        "-1",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(playlist_path),
        "-an",
        "-c:v",
        "copy",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        rtsp_url,
    ]


def sequence_command(
    *,
    ffmpeg: str,
    items: list[dict[str, Any]],
    rtsp_url: str,
    fps: float | None,
    width: int,
    height: int,
    pause_seconds: float,
    pause_image: Path,
) -> list[str]:
    cmd: list[str] = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
    ]
    input_count = 0
    for item in items:
        cmd.extend(["-re", "-i", str(Path(str(item["clip_path"])))])
        input_count += 1
        if pause_seconds > 0:
            cmd.extend(["-re", "-loop", "1", "-t", str(max(0.1, pause_seconds)), "-i", str(pause_image)])
            input_count += 1

    fps_filter = f",fps={max(1.0, float(fps or 10.0))}"
    filters: list[str] = []
    labels: list[str] = []
    for index in range(input_count):
        label = f"v{index}"
        labels.append(f"[{label}]")
        filters.append(
            f"[{index}:v]"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
            f"{fps_filter},setsar=1,format=yuv420p"
            f"[{label}]"
        )
    filters.append("".join(labels) + f"concat=n={input_count}:v=1:a=0[outv]")

    gop = max(10, int(round(float(fps or 10.0) * 2)))
    cmd.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-g",
            str(gop),
            "-pix_fmt",
            "yuv420p",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            rtsp_url,
        ]
    )
    return cmd


def filter_items(items: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == "all":
        return [item for item in items if item.get("clip_path")]
    if mode == "reviewed":
        return [item for item in items if item.get("clip_path") and item.get("expectation") in {"should_alarm", "should_not_alarm"}]
    if mode == "should_alarm":
        return [item for item in items if item.get("clip_path") and item.get("expectation") == "should_alarm"]
    if mode == "should_not_alarm":
        return [item for item in items if item.get("clip_path") and item.get("expectation") == "should_not_alarm"]
    if mode == "mixed":
        positives = [item for item in items if item.get("clip_path") and item.get("expectation") == "should_alarm"]
        negatives = [item for item in items if item.get("clip_path") and item.get("expectation") == "should_not_alarm"]
        mixed: list[dict[str, Any]] = []
        max_len = max(len(positives), len(negatives))
        for idx in range(max_len):
            if idx < len(positives):
                mixed.append(positives[idx])
            if idx < len(negatives):
                mixed.append(negatives[idx])
        return mixed
    return []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish clips one-by-one with quiet pauses between them.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--segment-cache-dir", type=Path, default=DEFAULT_SEGMENT_CACHE_DIR)
    parser.add_argument(
        "--publish-mode",
        choices=["playlist", "filter"],
        default="playlist",
        help="playlist keeps real-time playback; filter is the legacy concat-filter mode.",
    )
    parser.add_argument(
        "--mode",
        choices=["mixed", "reviewed", "all", "should_alarm", "should_not_alarm"],
        default="mixed",
    )
    parser.add_argument("--pause-seconds", type=float, default=30.0)
    parser.add_argument("--pause-image", type=Path, default=None)
    parser.add_argument("--pause-title", default=DEFAULT_PAUSE_LINES[0])
    parser.add_argument("--pause-subtitle", default=DEFAULT_PAUSE_LINES[1])
    parser.add_argument("--pause-footer", default=DEFAULT_PAUSE_LINES[2])
    parser.add_argument("--shuffle", action="store_true")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Number of clips per ffmpeg concat batch. Use 0 to publish all clips in one command.",
    )
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--start-with-pause", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg not found in PATH.", file=sys.stderr)
        return 2

    manifest = load_manifest(args.manifest)
    items = filter_items(list(manifest.get("items") or []), args.mode)
    if args.shuffle:
        random.shuffle(items)
    if args.max_items is not None:
        items = items[: max(0, int(args.max_items))]
    if not items:
        print("No clips selected for sequence.", file=sys.stderr)
        return 1

    pause_lines = [args.pause_title, args.pause_subtitle, args.pause_footer]
    pause_image = args.pause_image if args.pause_image else pause_image_path(args.width, args.height, pause_lines)
    if not pause_image.exists():
        print(f"Pause image not found: {pause_image}", file=sys.stderr)
        return 2

    args.log_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.log_dir / f"sequence_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    stopping = {"value": False}

    def request_stop(signum, frame):  # noqa: ARG001
        stopping["value"] = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    def stop_requested() -> bool:
        return bool(stopping["value"])

    print(f"Publishing sequence to {args.rtsp_url}")
    print(
        f"Mode={args.mode} publish_mode={args.publish_mode} clips={len(items)} "
        f"pause={args.pause_seconds:.1f}s fps={args.fps:g}"
    )
    print(f"ffmpeg log: {log_path}")
    print("Press Ctrl+C to stop.")

    if args.publish_mode == "playlist":
        print("[prepare] building normalized real-time playlist")
        try:
            playlist_path = prepare_playlist_segments(
                ffmpeg=ffmpeg,
                items=items,
                width=args.width,
                height=args.height,
                fps=args.fps,
                pause_seconds=args.pause_seconds,
                pause_image=pause_image,
                cache_dir=args.segment_cache_dir,
                log_path=log_path,
            )
        except subprocess.CalledProcessError as exc:
            print(f"Failed to prepare playlist segment. ffmpeg exit={exc.returncode}. See {log_path}", file=sys.stderr)
            return int(exc.returncode or 1)

        print(f"[prepare] playlist ready: {playlist_path}")
        try:
            while not stop_requested():
                return_code = run_ffmpeg_segment(
                    playlist_command(ffmpeg=ffmpeg, playlist_path=playlist_path, rtsp_url=args.rtsp_url),
                    log_path,
                    stop_requested,
                )
                if return_code == 130 or stop_requested():
                    break
                print(f"[publisher] ffmpeg exited with code {return_code}; restarting. See {log_path}")
                time.sleep(2.0)
        finally:
            print("\nSequence publisher stopped.")
        return 0

    chunks = iter_chunks(items, int(args.chunk_size))
    print(f"[filter] chunks={len(chunks)} chunk_size={args.chunk_size}")

    try:
        while not stop_requested():
            for chunk_index, chunk in enumerate(chunks, start=1):
                if stop_requested():
                    break
                for item in chunk:
                    print(
                        f"[clip] {item.get('replay_id')} "
                        f"expectation={item.get('expectation')} label={item.get('feedback_label')}"
                    )
                    if args.pause_seconds > 0:
                        print(f"[pause] {args.pause_seconds:.1f}s")
                print(f"[chunk] starting {chunk_index}/{len(chunks)} clips={len(chunk)}")
                return_code = run_ffmpeg_segment(
                    sequence_command(
                        ffmpeg=ffmpeg,
                        items=chunk,
                        rtsp_url=args.rtsp_url,
                        fps=args.fps,
                        width=args.width,
                        height=args.height,
                        pause_seconds=args.pause_seconds,
                        pause_image=pause_image,
                    ),
                    log_path,
                    stop_requested,
                )
                if return_code not in {0, 130}:
                    print(f"[chunk] ffmpeg exited with code {return_code}; see {log_path}")
                    time.sleep(2.0)
            print("[cycle] sequence finished; restarting from first chunk")
    finally:
        print("\nSequence publisher stopped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
