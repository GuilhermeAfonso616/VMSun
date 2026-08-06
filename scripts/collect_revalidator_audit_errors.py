r"""Coleta imagens dos erros gerados pela auditoria do revalidador IA2.

Entrada esperada: CSV criado por audit_latest_reviewed_events_with_revalidator.py.

Exemplo no Docker/Linux:

    python -B scripts/collect_revalidator_audit_errors.py \
      --audit-csv /app/exports/revalidator_audit/revalidator_audit_latest_400_20260504_211632.csv \
      --output-dir /app/exports/revalidator_audit_errors

O script cria pastas por tipo de erro, com crop, contexto com bbox e metadata.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


FIELDNAMES = [
    "event_id",
    "camera_id",
    "truth_class",
    "pred_class",
    "p_person",
    "p_not_person",
    "human_label",
    "reviewed_at",
    "snapshot_path",
    "resolved_snapshot_path",
    "bbox",
    "crop_path",
    "context_path",
    "metadata_path",
    "collect_status",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copia crops/contextos dos erros da auditoria IA2 para revisao e hard mining."
    )
    parser.add_argument("--audit-csv", required=True, help="CSV latest ou mismatches gerado pela auditoria.")
    parser.add_argument("--output-dir", default="exports/revalidator_audit_errors")
    parser.add_argument(
        "--error-type",
        choices=("all", "person_to_not_person", "not_person_to_person"),
        default="all",
    )
    parser.add_argument("--camera-id", type=int, help="Filtra uma camera especifica, ex: --camera-id 10.")
    parser.add_argument("--limit", type=int, help="Limita a quantidade de erros coletados.")
    parser.add_argument(
        "--path-map",
        action="append",
        default=[],
        help="Mapeia prefixos de path, ex: --path-map /data=/home/user/projeto/data. Pode repetir.",
    )
    parser.add_argument("--margin-pct", type=float, default=0.20)
    return parser.parse_args()


def safe_slug(value: Any, default: str = "unknown") -> str:
    text = str(value or default).strip().lower()
    chars = []
    for char in text:
        if char.isalnum() or char in {"-", "_"}:
            chars.append(char)
        elif char in {" ", "/", "\\", ".", ":"}:
            chars.append("_")
    cleaned = "".join(chars).strip("_")
    return cleaned or default


def parse_path_maps(values: list[str]) -> list[tuple[str, Path]]:
    maps: list[tuple[str, Path]] = []
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--path-map invalido: {value}. Use origem=destino.")
        source, target = value.split("=", 1)
        source = source.rstrip("/\\")
        maps.append((source, Path(target)))
    return maps


def resolve_snapshot(path_value: str | None, path_maps: list[tuple[str, Path]]) -> Path | None:
    if not path_value:
        return None
    original = Path(path_value)
    candidates = [original]
    raw = str(path_value)
    for source, target in path_maps:
        if raw == source or raw.startswith(source + "/") or raw.startswith(source + "\\"):
            suffix = raw[len(source):].lstrip("/\\")
            candidates.append(target / suffix)

    if raw.startswith("/data/"):
        candidates.append(PROJECT_ROOT / "data" / raw.removeprefix("/data/"))
    if not original.is_absolute():
        candidates.append(PROJECT_ROOT / original)

    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved.exists():
            return resolved
    return None


def parse_bbox(value: str | None) -> list[float] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        return None
    if isinstance(parsed, list) and len(parsed) == 4:
        try:
            return [float(item) for item in parsed]
        except (TypeError, ValueError):
            return None
    return None


def crop_with_margin(frame, bbox: list[float], margin_pct: float):
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    box_w = max(1.0, x2 - x1)
    box_h = max(1.0, y2 - y1)
    left = max(0, int(round(x1 - box_w * margin_pct)))
    top = max(0, int(round(y1 - box_h * margin_pct)))
    right = min(width, int(round(x2 + box_w * margin_pct)))
    bottom = min(height, int(round(y2 + box_h * margin_pct)))
    if right <= left or bottom <= top:
        return None
    crop = frame[top:bottom, left:right]
    return crop if crop is not None and crop.size > 0 else None


def draw_context(frame, bbox: list[float] | None, label: str):
    output = frame.copy()
    if bbox and len(bbox) == 4:
        height, width = output.shape[:2]
        x1, y1, x2, y2 = [int(round(value)) for value in bbox]
        x1 = max(0, min(width - 1, x1))
        y1 = max(0, min(height - 1, y1))
        x2 = max(0, min(width - 1, x2))
        y2 = max(0, min(height - 1, y2))
        if x2 > x1 and y2 > y1:
            cv2.rectangle(output, (x1, y1), (x2, y2), (0, 255, 255), 2)
            cv2.putText(output, label, (x1, max(15, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    return output


def iter_error_rows(path: Path, *, error_type: str, camera_id: int | None):
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            truth = row.get("truth_class")
            pred = row.get("pred_class")
            if truth == pred:
                continue
            if error_type == "person_to_not_person" and not (truth == "person" and pred == "not_person"):
                continue
            if error_type == "not_person_to_person" and not (truth == "not_person" and pred == "person"):
                continue
            if camera_id is not None:
                try:
                    if int(row.get("camera_id") or -1) != camera_id:
                        continue
                except ValueError:
                    continue
            yield row


def collect_one(row: dict[str, Any], *, output_dir: Path, path_maps: list[tuple[str, Path]], margin_pct: float) -> dict[str, Any]:
    truth = row.get("truth_class") or "unknown"
    pred = row.get("pred_class") or "unknown"
    bucket = f"{safe_slug(truth)}_pred_{safe_slug(pred)}"
    event_id = row.get("event_id") or "unknown"
    camera_id = row.get("camera_id") or "unknown"
    p_person = row.get("p_person") or "na"
    stem = f"event{event_id}_cam{camera_id}_p{safe_slug(p_person)}"

    bucket_dir = output_dir / bucket
    crops_dir = bucket_dir / "crops"
    context_dir = bucket_dir / "context"
    metadata_dir = bucket_dir / "metadata"
    for directory in (crops_dir, context_dir, metadata_dir):
        directory.mkdir(parents=True, exist_ok=True)

    snapshot = resolve_snapshot(row.get("snapshot_path"), path_maps)
    bbox = parse_bbox(row.get("bbox"))
    crop_path = crops_dir / f"{stem}_crop.jpg"
    context_path = context_dir / f"{stem}_context.jpg"
    metadata_path = metadata_dir / f"{stem}.json"
    status = "metadata_only"

    if snapshot is not None:
        frame = cv2.imread(str(snapshot))
        if frame is None:
            status = "snapshot_unreadable"
        else:
            context = draw_context(frame, bbox, f"{truth}->{pred}")
            cv2.imwrite(str(context_path), context)
            status = "context_saved"
            if bbox:
                crop = crop_with_margin(frame, bbox, margin_pct)
                if crop is not None:
                    cv2.imwrite(str(crop_path), crop)
                    status = "crop_saved"
    else:
        status = "snapshot_missing"

    metadata = dict(row)
    metadata.update(
        {
            "collect_status": status,
            "resolved_snapshot_path": str(snapshot) if snapshot else None,
            "crop_path": str(crop_path.relative_to(output_dir)) if crop_path.exists() else None,
            "context_path": str(context_path.relative_to(output_dir)) if context_path.exists() else None,
            "metadata_path": str(metadata_path.relative_to(output_dir)),
        }
    )
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def write_index(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    index_path = output_dir / "index.csv"
    with index_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(rows),
        "by_error": dict(Counter(f"{row.get('truth_class')}->{row.get('pred_class')}" for row in rows)),
        "by_camera": dict(Counter(str(row.get("camera_id")) for row in rows)),
        "by_status": dict(Counter(str(row.get("collect_status")) for row in rows)),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    audit_csv = Path(args.audit_csv)
    if not audit_csv.exists():
        raise SystemExit(f"CSV nao encontrado: {audit_csv}")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path_maps = parse_path_maps(args.path_map)

    collected: list[dict[str, Any]] = []
    for row in iter_error_rows(audit_csv, error_type=args.error_type, camera_id=args.camera_id):
        collected.append(collect_one(row, output_dir=output_dir, path_maps=path_maps, margin_pct=args.margin_pct))
        if args.limit and len(collected) >= args.limit:
            break

    write_index(output_dir, collected)
    print(f"Erros coletados: {len(collected)}")
    print(f"Saida: {output_dir.resolve()}")
    print(f"Resumo: {(output_dir / 'summary.json').resolve()}")
    print(f"Indice: {(output_dir / 'index.csv').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
