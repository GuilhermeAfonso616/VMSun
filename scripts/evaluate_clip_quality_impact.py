"""Compare IA1 detections on reviewed clips across reduced video qualities.

This script is meant for the real/false-positive replay clips exported from
OneDrive. It samples frames from each clip, runs the IA1 detector on the source
frame and on degraded variants, then reports whether lower resolution/JPEG
quality would likely lose true positives or keep false positives.

Examples:

    python -B scripts/evaluate_clip_quality_impact.py \
        --manifest data/test_replay/clip_replay_manifest.json \
        --model /models/ia1_candidate/ia1_candidate_vms_hardneg_v3_2_1024.pt

    python -B scripts/evaluate_clip_quality_impact.py \
        --source-dir "/mnt/analitico_ssd/Analitico VMS Clips" \
        --variant source \
        --variant ia_960=960x540:q80 \
        --variant ia_640=640x360:q70
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST = Path("data/test_replay/clip_replay_manifest.json")
DEFAULT_SOURCE_DIR = Path(r"D:\IA_Rebuild\Analitico VMS Clips")
DEFAULT_OUTPUT_ROOT = Path("reports/clip_quality_impact")
DEFAULT_VARIANTS = [
    "source",
    "ia_960=960x540:q80",
    "ia_640=640x360:q80",
    "ia_640_q65=640x360:q65",
    "ia_480_q65=480x270:q65",
]


@dataclass(frozen=True)
class QualityVariant:
    name: str
    width: int | None = None
    height: int | None = None
    jpeg_quality: int | None = None

    @property
    def is_source(self) -> bool:
        return self.width is None or self.height is None


def _read_env_file_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
    except Exception:
        return None
    return None


def default_model_path() -> Path:
    configured = os.environ.get("DETECTOR_MODEL_PATH")
    if configured:
        return Path(configured)
    for env_path in (Path(".env.docker"), Path(".env")):
        configured = _read_env_file_value(env_path, "DETECTOR_MODEL_PATH")
        if configured:
            return Path(configured)
    return Path("models/ia1_candidate/ia1_candidate_vms_hardneg_v3_2_1024.pt")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_manifest_from_source(source_dir: Path, limit: int | None) -> dict[str, Any]:
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from build_clip_replay_manifest import build_manifest

    return build_manifest(
        source_dir=source_dir,
        output_dir=DEFAULT_OUTPUT_ROOT,
        rtsp_base_url="rtsp://localhost:8554",
        include_unreviewed=False,
        limit=limit,
        probe_duration=False,
    )


def parse_variant(raw: str) -> QualityVariant:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("variant vazio")
    if value.lower() == "source":
        return QualityVariant(name="source")

    if "=" in value:
        name, spec = value.split("=", 1)
        name = name.strip()
    else:
        spec = value
        name = value

    match = re.fullmatch(r"(?P<w>\d+)x(?P<h>\d+)(?::q(?P<q>\d+))?", spec.strip().lower())
    if not match:
        raise ValueError(
            f"variant invalido: {raw!r}. Use source ou nome=640x360:q70."
        )

    width = int(match.group("w"))
    height = int(match.group("h"))
    quality = int(match.group("q") or 80)
    if width <= 0 or height <= 0:
        raise ValueError(f"variant com tamanho invalido: {raw!r}")
    if not 1 <= quality <= 100:
        raise ValueError(f"jpeg quality precisa ficar entre 1 e 100: {raw!r}")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip() or f"{width}x{height}_q{quality}")
    return QualityVariant(name=safe_name, width=width, height=height, jpeg_quality=quality)


def normalize_variants(raw_variants: list[str]) -> list[QualityVariant]:
    variants = [parse_variant(raw) for raw in (raw_variants or DEFAULT_VARIANTS)]
    if not any(variant.name == "source" for variant in variants):
        variants.insert(0, QualityVariant(name="source"))

    seen: set[str] = set()
    unique: list[QualityVariant] = []
    for variant in variants:
        if variant.name in seen:
            continue
        seen.add(variant.name)
        unique.append(variant)
    return unique


def degrade_frame(frame: Any, variant: QualityVariant) -> Any:
    if variant.is_source:
        return frame
    resized = cv2.resize(frame, (int(variant.width), int(variant.height)), interpolation=cv2.INTER_AREA)
    quality = int(variant.jpeg_quality or 80)
    ok, encoded = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return resized
    decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    return decoded if decoded is not None else resized


def person_scores(model: YOLO, frame: Any, *, conf: float, imgsz: int) -> list[float]:
    result = model.predict(frame, conf=conf, imgsz=imgsz, classes=[0], verbose=False)[0]
    if result.boxes is None:
        return []
    scores: list[float] = []
    for box in result.boxes:
        try:
            scores.append(float(box.conf.detach().cpu().item()))
        except Exception:
            continue
    return scores


def sample_frame_indexes(capture: cv2.VideoCapture, *, sample_seconds: float, max_frames: int) -> list[int]:
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if fps <= 0:
        fps = 10.0
    if total_frames <= 0:
        return list(range(max_frames))

    step = max(1, int(round(fps * max(0.1, sample_seconds))))
    indexes = list(range(0, total_frames, step))
    if len(indexes) > max_frames:
        if max_frames <= 1:
            return [indexes[len(indexes) // 2]]
        stride = (len(indexes) - 1) / float(max_frames - 1)
        indexes = [indexes[int(round(i * stride))] for i in range(max_frames)]
    return sorted(set(indexes))


def read_frame_at(capture: cv2.VideoCapture, index: int) -> Any | None:
    capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(index)))
    ok, frame = capture.read()
    return frame if ok else None


def item_label(item: dict[str, Any]) -> str:
    expectation = str(item.get("expectation") or "").strip().lower()
    feedback = str(item.get("feedback_label") or "").strip().lower()
    if expectation == "should_alarm" or feedback == "true_positive":
        return "true_positive"
    if expectation == "should_not_alarm" or feedback == "false_positive":
        return "false_positive"
    return feedback or expectation or "unknown"


def aggregate_scores(scores_by_frame: list[dict[str, Any]], hit_conf: float) -> dict[str, Any]:
    top_scores = [float(row["top_score"]) for row in scores_by_frame]
    detections = [score for score in top_scores if score >= hit_conf]
    return {
        "sampled_frames": len(top_scores),
        "frames_with_detection": len(detections),
        "detected_clip": bool(detections),
        "max_score": max(top_scores) if top_scores else 0.0,
        "mean_top_score": (sum(top_scores) / len(top_scores)) if top_scores else 0.0,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def save_example_frame(path: Path, frame: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), frame)


def evaluate_items(
    *,
    model: YOLO,
    items: list[dict[str, Any]],
    variants: list[QualityVariant],
    conf: float,
    hit_conf: float,
    imgsz: int,
    sample_seconds: float,
    max_frames_per_clip: int,
    save_examples: bool,
    output_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    frame_rows: list[dict[str, Any]] = []
    clip_rows: list[dict[str, Any]] = []

    total = len(items)
    for item_index, item in enumerate(items, start=1):
        clip_path = Path(str(item.get("clip_path") or ""))
        if not clip_path.exists():
            print(f"[skip] clip nao encontrado: {clip_path}")
            continue

        label = item_label(item)
        event_id = item.get("source_event_id") or item.get("replay_id") or item_index
        capture = cv2.VideoCapture(str(clip_path))
        if not capture.isOpened():
            print(f"[skip] nao abriu clip: {clip_path}")
            continue

        indexes = sample_frame_indexes(
            capture,
            sample_seconds=sample_seconds,
            max_frames=max_frames_per_clip,
        )
        print(f"[{item_index}/{total}] event={event_id} label={label} frames={len(indexes)}")

        clip_variant_frames: dict[str, list[dict[str, Any]]] = {variant.name: [] for variant in variants}
        for frame_number in indexes:
            frame = read_frame_at(capture, frame_number)
            if frame is None:
                continue
            for variant in variants:
                processed = degrade_frame(frame, variant)
                scores = person_scores(model, processed, conf=conf, imgsz=imgsz)
                top_score = max(scores) if scores else 0.0
                row = {
                    "event_id": event_id,
                    "replay_id": item.get("replay_id"),
                    "label": label,
                    "clip_path": str(clip_path),
                    "variant": variant.name,
                    "width": int(processed.shape[1]),
                    "height": int(processed.shape[0]),
                    "jpeg_quality": variant.jpeg_quality if variant.jpeg_quality is not None else "",
                    "frame_number": int(frame_number),
                    "detections": len(scores),
                    "top_score": round(float(top_score), 6),
                    "hit": bool(top_score >= hit_conf),
                }
                frame_rows.append(row)
                clip_variant_frames[variant.name].append(row)

        capture.release()

        source_summary = aggregate_scores(clip_variant_frames.get("source", []), hit_conf)
        for variant in variants:
            summary = aggregate_scores(clip_variant_frames.get(variant.name, []), hit_conf)
            source_max = float(source_summary["max_score"])
            max_score = float(summary["max_score"])
            lost_vs_source = bool(source_summary["detected_clip"] and not summary["detected_clip"])
            missed_true_positive = bool(label == "true_positive" and not summary["detected_clip"])
            false_positive_detected = bool(label == "false_positive" and summary["detected_clip"])
            clip_row = {
                "event_id": event_id,
                "replay_id": item.get("replay_id"),
                "label": label,
                "clip_path": str(clip_path),
                "variant": variant.name,
                "variant_width": variant.width or "",
                "variant_height": variant.height or "",
                "jpeg_quality": variant.jpeg_quality or "",
                **summary,
                "source_detected_clip": bool(source_summary["detected_clip"]),
                "source_max_score": round(source_max, 6),
                "score_delta_vs_source": round(max_score - source_max, 6),
                "score_ratio_vs_source": round(max_score / source_max, 6) if source_max > 0 else "",
                "lost_vs_source": lost_vs_source,
                "missed_true_positive": missed_true_positive,
                "false_positive_detected": false_positive_detected,
            }
            clip_rows.append(clip_row)

            if save_examples and variant.name != "source" and (lost_vs_source or missed_true_positive):
                frame = None
                capture = cv2.VideoCapture(str(clip_path))
                if capture.isOpened() and indexes:
                    frame = read_frame_at(capture, indexes[len(indexes) // 2])
                capture.release()
                if frame is not None:
                    example = degrade_frame(frame, variant)
                    save_example_frame(output_dir / "examples" / f"event_{event_id}_{variant.name}.jpg", example)

    return frame_rows, clip_rows


def summarize_by_variant(clip_rows: list[dict[str, Any]]) -> dict[str, Any]:
    variants = sorted({str(row["variant"]) for row in clip_rows})
    summary: dict[str, Any] = {}
    for variant in variants:
        rows = [row for row in clip_rows if row["variant"] == variant]
        tp = [row for row in rows if row["label"] == "true_positive"]
        fp = [row for row in rows if row["label"] == "false_positive"]
        detected_tp = sum(1 for row in tp if row["detected_clip"])
        missed_tp = sum(1 for row in tp if row["missed_true_positive"])
        detected_fp = sum(1 for row in fp if row["false_positive_detected"])
        losses = sum(1 for row in rows if row["lost_vs_source"])
        max_scores = [float(row["max_score"]) for row in rows]
        summary[variant] = {
            "clips": len(rows),
            "true_positive_clips": len(tp),
            "true_positive_detected": detected_tp,
            "true_positive_missed": missed_tp,
            "true_positive_detection_rate": round(detected_tp / len(tp), 6) if tp else None,
            "false_positive_clips": len(fp),
            "false_positive_detected": detected_fp,
            "false_positive_detection_rate": round(detected_fp / len(fp), 6) if fp else None,
            "lost_vs_source": losses,
            "mean_max_score": round(sum(max_scores) / len(max_scores), 6) if max_scores else 0.0,
        }
    return summary


def write_markdown_report(path: Path, *, model_path: Path, args: argparse.Namespace, summary: dict[str, Any]) -> None:
    lines = [
        "# Impacto de qualidade nos clipes",
        "",
        f"Modelo: `{model_path}`",
        f"Conf detector: `{args.conf}`",
        f"Hit conf: `{args.hit_conf}`",
        f"Imgsz: `{args.imgsz}`",
        f"Amostragem: 1 frame a cada `{args.sample_seconds}`s, max `{args.max_frames_per_clip}` por clipe",
        "",
        "## Resumo por variante",
        "",
        "| Variante | TP detectados | TP perdidos | FP detectados | Perdas vs original | Score medio max |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for variant, data in summary.items():
        tp_text = f"{data['true_positive_detected']}/{data['true_positive_clips']}"
        fp_text = f"{data['false_positive_detected']}/{data['false_positive_clips']}"
        lines.append(
            "| "
            f"{variant} | "
            f"{tp_text} | "
            f"{data['true_positive_missed']} | "
            f"{fp_text} | "
            f"{data['lost_vs_source']} | "
            f"{data['mean_max_score']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Como ler",
            "",
            "- TP perdido: clipe marcado como verdadeiro positivo que nao teve deteccao acima do hit-conf.",
            "- Perda vs original: a variante perdeu deteccao que existia no frame original.",
            "- FP detectado: clipe marcado como falso positivo ainda teve deteccao de pessoa acima do hit-conf.",
            "",
            "Observacao: a compressao JPEG aqui usa qualidade 1-100 do OpenCV; nao e a mesma escala exata do `-q:v` do ffmpeg, mas serve para comparar tendencia.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Avalia perda de deteccao em clipes com qualidade reduzida.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-dir", type=Path, default=None, help="Opcional: monta manifesto direto da pasta de clips.")
    parser.add_argument("--model", type=Path, default=default_model_path())
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--variant", action="append", default=None, help="source ou nome=640x360:q70. Pode repetir.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--label", action="append", choices=["true_positive", "false_positive", "unknown"], default=None)
    parser.add_argument("--sample-seconds", type=float, default=1.0)
    parser.add_argument("--max-frames-per-clip", type=int, default=12)
    parser.add_argument("--conf", type=float, default=0.05)
    parser.add_argument("--hit-conf", type=float, default=0.05)
    parser.add_argument("--imgsz", type=int, default=1024)
    parser.add_argument("--save-examples", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    global cv2, YOLO
    try:
        import cv2
        from ultralytics import YOLO
    except Exception as exc:
        raise RuntimeError(
            "Dependencias ausentes. Rode dentro do container/ambiente do projeto "
            "com opencv-python-headless e ultralytics instalados."
        ) from exc

    if args.source_dir:
        manifest = build_manifest_from_source(args.source_dir, args.limit)
    else:
        if not args.manifest.exists():
            raise FileNotFoundError(f"Manifest nao encontrado: {args.manifest}")
        manifest = load_json(args.manifest)

    items = list(manifest.get("items") or [])
    if args.label:
        allowed = set(args.label)
        items = [item for item in items if item_label(item) in allowed]
    if args.limit is not None:
        items = items[: max(0, int(args.limit))]
    if not items:
        raise RuntimeError("Nenhum clip para avaliar.")
    if not args.model.exists():
        raise FileNotFoundError(f"Modelo nao encontrado: {args.model}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or (DEFAULT_OUTPUT_ROOT / f"quality_impact_{stamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    variants = normalize_variants(args.variant or DEFAULT_VARIANTS)
    print("Modelo:", args.model)
    print("Clips:", len(items))
    print("Variantes:", ", ".join(variant.name for variant in variants))
    model = YOLO(str(args.model))

    frame_rows, clip_rows = evaluate_items(
        model=model,
        items=items,
        variants=variants,
        conf=float(args.conf),
        hit_conf=float(args.hit_conf),
        imgsz=int(args.imgsz),
        sample_seconds=float(args.sample_seconds),
        max_frames_per_clip=int(args.max_frames_per_clip),
        save_examples=bool(args.save_examples),
        output_dir=output_dir,
    )

    summary = summarize_by_variant(clip_rows)
    write_csv(output_dir / "quality_frame_results.csv", frame_rows)
    write_csv(output_dir / "quality_clip_results.csv", clip_rows)
    (output_dir / "quality_summary.json").write_text(
        json.dumps(
            {
                "model": str(args.model),
                "items": len(items),
                "variants": [variant.__dict__ for variant in variants],
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_markdown_report(output_dir / "quality_summary.md", model_path=args.model, args=args, summary=summary)

    print("Relatorio:", output_dir / "quality_summary.md")
    print("CSV clipes:", output_dir / "quality_clip_results.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
