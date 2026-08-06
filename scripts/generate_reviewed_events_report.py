from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

try:
    from app.core.config import settings
except Exception:  # pragma: no cover - script can still run with explicit paths.
    settings = None


DETAIL_FLOAT_PATTERNS = {
    "ia2_person_score": r"revalidator_person=([0-9.]+)",
    "ia2_threshold": r"revalidator_person=[0-9.]+\s+threshold=([0-9.]+)",
    "ia3_person_score": r"far_revalidator_person=([0-9.]+)",
    "ia3_threshold": r"far_revalidator_person=[0-9.]+\s+threshold=([0-9.]+)",
}

FALSE_POSITIVE_LABELS = {"false_positive", "not_person", "nao_pessoa", "fp"}
TRUE_POSITIVE_LABELS = {"true_positive", "person", "pessoa", "expected_event"}


def _default_base_dir() -> Path:
    if settings is not None:
        try:
            return Path(settings.app_base_dir)
        except Exception:
            pass
    return Path.cwd()


def _default_database_url() -> str:
    if settings is not None:
        return str(settings.database_url)
    return f"sqlite:///{(_default_base_dir() / 'data' / 'analytics.db').resolve().as_posix()}"


def _sqlite_path_from_url(database_url: str) -> Path:
    if database_url.startswith("sqlite:///"):
        return Path(database_url.removeprefix("sqlite:///"))
    if database_url.startswith("sqlite://"):
        return Path(database_url.removeprefix("sqlite://"))
    return Path(database_url)


def _parse_datetime(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _fmt_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return None


def _extract_detail_float(details: str | None, pattern: str) -> float | None:
    match = re.search(pattern, str(details or ""))
    if not match:
        return None
    return _safe_float(match.group(1))


def extract_ai_scores(row: dict[str, Any]) -> dict[str, Any]:
    details = str(row.get("details") or "")
    result: dict[str, Any] = {
        "ia1_detector_score": _safe_float(row.get("detector_score")),
        "event_score": _safe_float(row.get("event_score")),
    }
    for key, pattern in DETAIL_FLOAT_PATTERNS.items():
        result[key] = _extract_detail_float(details, pattern)

    result["ia2_status"] = _classify_ia2(result.get("ia2_person_score"), result.get("ia2_threshold"))
    result["ia3_status"] = _classify_ia3(result.get("ia3_person_score"), result.get("ia3_threshold"))
    result["strategy3_v2_decision"] = _extract_json_path(row.get("details"), "strategy3_v2", "decision")
    result["notification_decision"] = _extract_json_path(row.get("details"), "notification_decision")
    return result


def _extract_json_path(details: Any, *path: str) -> Any:
    parsed = _json_loads(details)
    if not isinstance(parsed, dict):
        return ""
    value: Any = parsed
    for item in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(item)
    return value if value is not None else ""


def _classify_ia2(score: float | None, threshold: float | None) -> str:
    if score is None:
        return "sem_dados"
    if score >= 0.5:
        return "confirmou_pessoa"
    if threshold is not None and score >= threshold:
        return "negou_visual_mas_passou_politica_conservadora"
    return "negou_pessoa"


def _classify_ia3(score: float | None, threshold: float | None) -> str:
    if score is None:
        return "nao_aplicada"
    if threshold is not None and score < threshold:
        return "negou_pessoa_forte"
    if score < 0.05:
        return "negou_pessoa"
    if score >= 0.5:
        return "confirmou_pessoa"
    return "incerta"


def normalize_label(label: str | None) -> str:
    value = str(label or "").strip().lower()
    if value in FALSE_POSITIVE_LABELS:
        return "false_positive"
    if value in TRUE_POSITIVE_LABELS:
        return "true_positive"
    return value or "unlabeled"


def infer_false_positive_reason(row: dict[str, Any]) -> str:
    label = normalize_label(row.get("feedback_label"))
    if label != "false_positive":
        return ""

    cause = str(row.get("probable_cause") or "").strip()
    ia2 = _safe_float(row.get("ia2_person_score"))
    ia3 = _safe_float(row.get("ia3_person_score"))
    detector = _safe_float(row.get("ia1_detector_score"))
    bbox_area_ratio = _bbox_area_ratio(row.get("bbox_json"))
    note = str(row.get("operator_note") or "").strip()

    reasons: list[str] = []
    if cause:
        reasons.append(f"feedback_causa={cause}")
    if detector is not None and detector >= 0.65:
        reasons.append("IA1/detector abriu suspeita forte")
    if ia2 is not None and ia2 < 0.25:
        reasons.append("IA2 nao confirmou pessoa")
    if ia3 is not None and ia3 < 0.005:
        reasons.append("IA3 negou pessoa muito forte")
    if ia2 is None and ia3 is None:
        reasons.append("sem scores IA2/IA3 nos details persistidos")
    if bbox_area_ratio is not None and bbox_area_ratio < 0.01:
        reasons.append("bbox pequena favorece falso positivo")
    if note:
        reasons.append(f"nota_operador={note[:80]}")

    if not reasons:
        return "Falso positivo revisado; precisa olhar snapshot/nota para causa visual."
    return "; ".join(reasons)


def _bbox_area_ratio(bbox_json: Any) -> float | None:
    bbox = _json_loads(bbox_json)
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        x1, y1, x2, y2 = [float(v) for v in bbox]
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        # Frame size is not always persisted with the event row, so this is only
        # a rough normalized proxy for ranking tiny boxes.
        return area / max(1.0, 704.0 * 480.0)
    except Exception:
        return None


def recommended_action(row: dict[str, Any]) -> str:
    label = normalize_label(row.get("feedback_label"))
    if label != "false_positive":
        return ""

    ia2 = _safe_float(row.get("ia2_person_score"))
    ia3 = _safe_float(row.get("ia3_person_score"))
    cause = str(row.get("probable_cause") or "").strip()

    if ia3 is not None and ia3 < 0.005 and (ia2 is None or ia2 < 0.25):
        return "criar regra consenso: IA3 nega forte + IA2 fraca => SUPPRESS/AUDIT"
    if cause in {"glass_reflection", "shadow", "vegetation_wind", "headlights"}:
        return "alimentar region_memory/blacklist por camera/regiao e rebaixar notificacao"
    if cause in {"small_target", "threshold_too_low"}:
        return "aumentar filtro de bbox/score para essa camera ou exigir tracking/temporal"
    return "usar como amostra anti-FP para calibracao por camera/regiao"


def load_reviewed_events(database_url: str, since: datetime, until: datetime, only_reviewed: bool = True) -> list[dict[str, Any]]:
    db_path = _sqlite_path_from_url(database_url)
    if not db_path.exists():
        raise FileNotFoundError(f"Banco nao encontrado: {db_path}")

    since_text = _fmt_dt(since)
    until_text = _fmt_dt(until)
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        where_feedback = "AND lf.feedback_id IS NOT NULL" if only_reviewed else ""
        rows = conn.execute(
            f"""
            WITH latest_feedback AS (
                SELECT
                    ef.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY ef.event_id
                        ORDER BY ef.reviewed_at DESC, ef.id DESC
                    ) AS rn
                FROM event_feedback ef
            ),
            lf AS (
                SELECT
                    id AS feedback_id,
                    event_id,
                    camera_id AS feedback_camera_id,
                    label AS feedback_label,
                    probable_cause,
                    operator_note,
                    reviewed_by,
                    reviewed_at
                FROM latest_feedback
                WHERE rn = 1
            )
            SELECT
                e.id AS event_id,
                e.camera_id,
                c.name AS camera_name,
                e.event_type,
                e.started_at,
                e.ended_at,
                e.created_at,
                e.track_id,
                e.detector_score,
                e.confidence,
                e.event_score,
                e.details,
                e.snapshot_path,
                e.clip_path,
                e.bbox_json,
                e.severity,
                e.status,
                e.alarm_eligible,
                e.lifecycle_action,
                e.is_alarm_active,
                e.rule_id,
                e.zone_id,
                e.roi_id,
                lf.feedback_id,
                lf.feedback_label,
                lf.probable_cause,
                lf.operator_note,
                lf.reviewed_by,
                lf.reviewed_at
            FROM events e
            LEFT JOIN cameras c ON c.id = e.camera_id
            LEFT JOIN lf ON lf.event_id = e.id
            WHERE COALESCE(e.started_at, e.created_at) >= ?
              AND COALESCE(e.started_at, e.created_at) <= ?
              {where_feedback}
            ORDER BY COALESCE(e.started_at, e.created_at), e.id
            """,
            (since_text, until_text),
        ).fetchall()
    finally:
        conn.close()

    enriched: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["feedback_label_normalized"] = normalize_label(item.get("feedback_label"))
        item.update(extract_ai_scores(item))
        item["bbox_area_ratio_proxy"] = _bbox_area_ratio(item.get("bbox_json"))
        item["false_positive_reason"] = infer_false_positive_reason(item)
        item["recommended_action"] = recommended_action(item)
        enriched.append(item)
    return enriched


def _csv_write(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _counter_table(counter: Counter, title_a: str, title_b: str) -> list[str]:
    lines = [f"| {title_a} | {title_b} |", "|---|---:|"]
    if not counter:
        lines.append("| nenhum | 0 |")
        return lines
    for key, count in counter.most_common():
        lines.append(f"| {key} | {count} |")
    return lines


def build_markdown(rows: list[dict[str, Any]], since: datetime, until: datetime, csv_paths: dict[str, Path]) -> str:
    labels = Counter(str(row.get("feedback_label_normalized") or "unlabeled") for row in rows)
    cameras = Counter(str(row.get("camera_name") or row.get("camera_id") or "-") for row in rows)
    causes = Counter(str(row.get("probable_cause") or "sem_causa") for row in rows if row.get("feedback_label_normalized") == "false_positive")
    actions = Counter(str(row.get("recommended_action") or "sem_acao") for row in rows if row.get("feedback_label_normalized") == "false_positive")

    false_positive_count = labels.get("false_positive", 0)
    true_positive_count = labels.get("true_positive", 0)
    reviewed_total = len(rows)
    precision = true_positive_count / reviewed_total if reviewed_total else 0.0

    lines = [
        "# Relatorio de reavaliacoes de eventos",
        "",
        f"- Janela analisada: `{_fmt_dt(since)}` ate `{_fmt_dt(until)}`",
        f"- Eventos reavaliados: `{reviewed_total}`",
        f"- Verdadeiros positivos: `{true_positive_count}`",
        f"- Falsos positivos: `{false_positive_count}`",
        f"- Precisao manual aproximada: `{precision:.1%}`",
        "",
        "## Resultado das reavaliacoes",
    ]
    lines.extend(_counter_table(labels, "Label", "Eventos"))
    lines.extend(["", "## Reavaliacoes por camera"])
    lines.extend(_counter_table(cameras, "Camera", "Eventos"))
    lines.extend(["", "## Causas dos falsos positivos"])
    lines.extend(_counter_table(causes, "Causa", "Eventos"))
    lines.extend(["", "## Acoes recomendadas para falsos positivos"])
    lines.extend(_counter_table(actions, "Acao", "Eventos"))

    lines.extend(["", "## Falsos positivos principais"])
    fps = [row for row in rows if row.get("feedback_label_normalized") == "false_positive"]
    if not fps:
        lines.append("- Nenhum falso positivo reavaliado nesta janela.")
    else:
        for row in fps[:80]:
            lines.append(
                f"- Evento `{row.get('event_id')}` camera `{row.get('camera_name') or row.get('camera_id')}` "
                f"score `{_safe_float(row.get('event_score')) or 0:.3f}` causa `{row.get('probable_cause') or 'sem_causa'}`"
            )
            lines.append(f"  - por que passou: {row.get('false_positive_reason') or 'sem diagnostico'}")
            lines.append(f"  - acao: {row.get('recommended_action') or 'sem acao'}")
            if row.get("snapshot_path"):
                lines.append(f"  - snapshot: `{row.get('snapshot_path')}`")

    lines.extend(
        [
            "",
            "## Arquivos gerados",
            f"- CSV completo: `{csv_paths['all']}`",
            f"- CSV falsos positivos: `{csv_paths['false_positive']}`",
            f"- CSV resumo por camera: `{csv_paths['by_camera']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def generate_report(args: argparse.Namespace) -> dict[str, Path]:
    until = _parse_datetime(args.until) if args.until else datetime.now()
    since = _parse_datetime(args.since) if args.since else until - timedelta(hours=float(args.hours))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = load_reviewed_events(args.database_url, since, until, only_reviewed=not bool(args.include_unreviewed))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    all_csv = output_dir / f"reviewed_events_{stamp}.csv"
    fp_csv = output_dir / f"reviewed_false_positives_{stamp}.csv"
    by_camera_csv = output_dir / f"reviewed_by_camera_{stamp}.csv"
    summary_md = output_dir / f"reviewed_events_summary_{stamp}.md"
    manifest_json = output_dir / f"reviewed_events_manifest_{stamp}.json"

    fieldnames = [
        "event_id",
        "camera_id",
        "camera_name",
        "event_type",
        "started_at",
        "status",
        "event_score",
        "ia1_detector_score",
        "ia2_person_score",
        "ia2_status",
        "ia3_person_score",
        "ia3_status",
        "feedback_label",
        "feedback_label_normalized",
        "probable_cause",
        "operator_note",
        "reviewed_by",
        "reviewed_at",
        "false_positive_reason",
        "recommended_action",
        "bbox_area_ratio_proxy",
        "track_id",
        "snapshot_path",
        "clip_path",
        "bbox_json",
    ]
    _csv_write(all_csv, rows, fieldnames)
    fp_rows = [row for row in rows if row.get("feedback_label_normalized") == "false_positive"]
    _csv_write(fp_csv, fp_rows, fieldnames)

    grouped: list[dict[str, Any]] = []
    by_camera: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("camera_name") or row.get("camera_id") or "-")
        by_camera.setdefault(key, []).append(row)
    for camera_name, camera_rows in sorted(by_camera.items()):
        labels = Counter(str(row.get("feedback_label_normalized") or "unlabeled") for row in camera_rows)
        grouped.append(
            {
                "camera_name": camera_name,
                "reviewed_events": len(camera_rows),
                "true_positive": labels.get("true_positive", 0),
                "false_positive": labels.get("false_positive", 0),
                "inconclusive": labels.get("inconclusive", 0),
                "precision_manual": round(labels.get("true_positive", 0) / max(1, len(camera_rows)), 4),
            }
        )
    _csv_write(by_camera_csv, grouped, ["camera_name", "reviewed_events", "true_positive", "false_positive", "inconclusive", "precision_manual"])

    summary_md.write_text(
        build_markdown(rows, since, until, {"all": all_csv, "false_positive": fp_csv, "by_camera": by_camera_csv}),
        encoding="utf-8",
    )

    manifest = {
        "summary": str(summary_md),
        "all_csv": str(all_csv),
        "false_positive_csv": str(fp_csv),
        "by_camera_csv": str(by_camera_csv),
        "since": _fmt_dt(since),
        "until": _fmt_dt(until),
        "reviewed_events": len(rows),
        "false_positive_events": len(fp_rows),
    }
    manifest_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": summary_md, "all": all_csv, "false_positive": fp_csv, "by_camera": by_camera_csv, "manifest": manifest_json}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gera relatorio dos resultados de reavaliacao manual de eventos.")
    parser.add_argument("--hours", type=float, default=48.0, help="Horas para olhar para tras quando --since nao for informado.")
    parser.add_argument("--since", default="", help="Inicio da janela. Ex: '2026-05-09 10:00:00'.")
    parser.add_argument("--until", default="", help="Fim da janela. Padrao: agora.")
    parser.add_argument("--database-url", default=_default_database_url(), help="URL sqlite do banco analytics.")
    parser.add_argument("--output-dir", default=str(_default_base_dir() / "reports" / "reviewed_events"), help="Pasta de saida.")
    parser.add_argument("--include-unreviewed", action="store_true", help="Inclui eventos sem feedback no CSV completo.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = generate_report(args)
    print(f"Resumo: {paths['summary']}")
    print(f"CSV completo: {paths['all']}")
    print(f"CSV falsos positivos: {paths['false_positive']}")
    print(f"CSV por camera: {paths['by_camera']}")
    print(f"Manifest: {paths['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
