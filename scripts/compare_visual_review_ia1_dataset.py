import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_DATASET = Path("D:/IA/datasets/ia1_finetune_vms_hardneg_v1")
DEFAULT_REVIEW = Path("D:/Analitico/reports/ia1_finetune_vms_hardneg_v1/visual_review_ia1_dataset.csv")
DEFAULT_REPORT_DIR = Path("D:/Analitico/reports/ia1_finetune_vms_hardneg_v1")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compara a revisao visual atual com o label esperado no manifest do dataset IA1."
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="Pasta do dataset IA1.")
    parser.add_argument("--review", default=str(DEFAULT_REVIEW), help="CSV gerado pela revisao visual.")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR), help="Pasta para salvar relatorios.")
    return parser.parse_args()


def read_csv(path):
    with Path(path).open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(fields)
    for row in rows:
        for key in row.keys():
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def expected_label(row):
    review_kind = row.get("review_kind", "")
    if review_kind == "positive":
        return "person"
    if review_kind == "negative":
        return "not_person"
    human_label = row.get("human_label", "")
    if human_label in {"true_positive", "person", "external_person"}:
        return "person"
    if human_label in {"false_positive", "not_person"}:
        return "not_person"
    return ""


def label_file_status(row):
    label_path = Path(row.get("label_path", ""))
    if not label_path.exists():
        return "missing"
    text = label_path.read_text(encoding="utf-8", errors="ignore").strip()
    return "non_empty" if text else "empty"


def latest_reviews(review_rows):
    latest_by_path = {}
    duplicates = []
    seen = Counter()
    for index, row in enumerate(review_rows, start=1):
        path = row.get("path", "")
        seen[path] += 1
        if seen[path] > 1:
            duplicates.append({**row, "review_row": index, "review_count_for_path": seen[path]})
        latest_by_path[path] = {**row, "review_row": index}
    return latest_by_path, duplicates


def enrich(manifest_row, review_row):
    expected = expected_label(manifest_row)
    reviewed_as = review_row.get("reviewed_as", "")
    label_status = label_file_status(manifest_row)
    return {
        "path": manifest_row.get("path", ""),
        "label_path": manifest_row.get("label_path", ""),
        "expected_label": expected,
        "reviewed_as": reviewed_as,
        "discrepancy_type": classify_discrepancy(expected, reviewed_as, label_status),
        "split": manifest_row.get("split", ""),
        "source": manifest_row.get("source", ""),
        "review_kind": manifest_row.get("review_kind", ""),
        "human_label": manifest_row.get("human_label", ""),
        "camera_id": manifest_row.get("camera_id", ""),
        "event_id": manifest_row.get("event_id", ""),
        "track_id": manifest_row.get("track_id", ""),
        "sha1": manifest_row.get("sha1", ""),
        "bbox_bucket": manifest_row.get("bbox_bucket", ""),
        "probable_cause": manifest_row.get("probable_cause", ""),
        "label_file_status": label_status,
        "review_row": review_row.get("review_row", ""),
        "note": review_row.get("note", ""),
    }


def classify_discrepancy(expected, reviewed_as, label_status):
    if expected == "person" and label_status in {"empty", "missing"}:
        return "positive_label_file_empty_or_missing"
    if expected == "not_person" and label_status == "non_empty":
        return "negative_label_file_non_empty"
    if reviewed_as == "wrong_bbox":
        return "wrong_bbox"
    if reviewed_as == "uncertain":
        return "uncertain"
    if expected and reviewed_as in {"person", "not_person"} and expected != reviewed_as:
        if expected == "person":
            return "expected_person_reviewed_not_person"
        return "expected_not_person_reviewed_person"
    if not expected:
        return "unknown_expected_label"
    return ""


def count_by(rows, *keys):
    counter = Counter(tuple(row.get(key, "") for key in keys) for row in rows)
    return [
        {**{key: values[i] for i, key in enumerate(keys)}, "count": count}
        for values, count in sorted(counter.items())
    ]


def markdown_table(rows, columns, limit=30):
    if not rows:
        return "_Nenhum._"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows[:limit]:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    if len(rows) > limit:
        lines.append(f"| ... | mais {len(rows) - limit} linhas |" + " |" * max(0, len(columns) - 2))
    return "\n".join(lines)


def build_report(report_path, summary, discrepancy_rows, uncertain_rows, duplicate_rows, missing_manifest_rows):
    by_type = count_by(discrepancy_rows, "discrepancy_type")
    by_camera = count_by(discrepancy_rows, "camera_id", "discrepancy_type")
    by_split = count_by(discrepancy_rows, "split", "discrepancy_type")
    by_source = count_by(discrepancy_rows, "source", "discrepancy_type")

    lines = [
        "# Comparacao da revisao visual IA1",
        "",
        "Este relatorio compara a avaliacao visual atual com o label usado no dataset do ultimo treino.",
        "",
        "## Resumo",
        "",
        f"- Imagens no manifest: {summary['manifest_rows']}",
        f"- Linhas revisadas no CSV: {summary['review_rows']}",
        f"- Imagens revisadas unicas: {summary['unique_reviewed_images']}",
        f"- Imagens revisadas encontradas no manifest: {summary['matched_reviewed_images']}",
        f"- Discrepancias criticas/correcoes: {summary['critical_discrepancies']}",
        f"- BBox errada: {summary['wrong_bbox']}",
        f"- Incertos: {summary['uncertain']}",
        f"- Revisões duplicadas: {summary['duplicate_review_rows']}",
        f"- Revisoes sem imagem correspondente no manifest: {summary['review_missing_from_manifest']}",
        "",
        "## Discrepancias Por Tipo",
        "",
        markdown_table(by_type, ["discrepancy_type", "count"]),
        "",
        "## Discrepancias Por Camera",
        "",
        markdown_table(by_camera, ["camera_id", "discrepancy_type", "count"], limit=50),
        "",
        "## Discrepancias Por Split",
        "",
        markdown_table(by_split, ["split", "discrepancy_type", "count"]),
        "",
        "## Discrepancias Por Fonte",
        "",
        markdown_table(by_source, ["source", "discrepancy_type", "count"]),
        "",
        "## Arquivos Gerados",
        "",
        "- `visual_review_discrepancies.csv`: imagens onde a revisao divergiu do dataset ou bbox/label precisa atencao.",
        "- `visual_review_uncertain.csv`: imagens marcadas como incertas.",
        "- `visual_review_duplicates.csv`: imagens revisadas mais de uma vez.",
        "- `visual_review_missing_from_manifest.csv`: revisoes que nao cruzaram com o manifest.",
        "",
        "## Regra de Seguranca",
        "",
        "Qualquer `expected_person_reviewed_not_person` deve ser revisado manualmente antes de remover positivo do treino. Para IA1, perder pessoa real e mais grave do que manter algum falso positivo.",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    args = parse_args()
    dataset_dir = Path(args.dataset)
    review_path = Path(args.review)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = dataset_dir / "manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.csv nao encontrado: {manifest_path}")
    if not review_path.exists():
        raise FileNotFoundError(f"CSV de revisao nao encontrado: {review_path}")

    manifest_rows = read_csv(manifest_path)
    review_rows = read_csv(review_path)
    manifest_by_path = {row.get("path", ""): row for row in manifest_rows}
    latest_by_path, duplicate_rows = latest_reviews(review_rows)

    discrepancy_rows = []
    uncertain_rows = []
    missing_manifest_rows = []
    matched = 0

    for path, review_row in latest_by_path.items():
        manifest_row = manifest_by_path.get(path)
        if not manifest_row:
            missing_manifest_rows.append(review_row)
            continue
        matched += 1
        enriched = enrich(manifest_row, review_row)
        dtype = enriched["discrepancy_type"]
        if dtype == "uncertain":
            uncertain_rows.append(enriched)
        elif dtype:
            discrepancy_rows.append(enriched)

    fields = [
        "path",
        "label_path",
        "expected_label",
        "reviewed_as",
        "discrepancy_type",
        "split",
        "source",
        "review_kind",
        "human_label",
        "camera_id",
        "event_id",
        "track_id",
        "sha1",
        "bbox_bucket",
        "probable_cause",
        "label_file_status",
        "review_row",
        "note",
    ]
    write_csv(report_dir / "visual_review_discrepancies.csv", discrepancy_rows, fields)
    write_csv(report_dir / "visual_review_uncertain.csv", uncertain_rows, fields)
    write_csv(report_dir / "visual_review_duplicates.csv", duplicate_rows, review_rows[0].keys() if review_rows else ["path"])
    write_csv(
        report_dir / "visual_review_missing_from_manifest.csv",
        missing_manifest_rows,
        review_rows[0].keys() if review_rows else ["path"],
    )

    critical_types = {
        "expected_person_reviewed_not_person",
        "expected_not_person_reviewed_person",
        "positive_label_file_empty_or_missing",
        "negative_label_file_non_empty",
    }
    summary = {
        "manifest_rows": len(manifest_rows),
        "review_rows": len(review_rows),
        "unique_reviewed_images": len(latest_by_path),
        "matched_reviewed_images": matched,
        "critical_discrepancies": sum(row["discrepancy_type"] in critical_types for row in discrepancy_rows),
        "wrong_bbox": sum(row["discrepancy_type"] == "wrong_bbox" for row in discrepancy_rows),
        "uncertain": len(uncertain_rows),
        "duplicate_review_rows": len(duplicate_rows),
        "review_missing_from_manifest": len(missing_manifest_rows),
    }

    (report_dir / "visual_review_comparison_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    build_report(report_dir / "visual_review_comparison.md", summary, discrepancy_rows, uncertain_rows, duplicate_rows, missing_manifest_rows)

    print("Comparacao concluida.")
    print(f"Revisadas unicas: {summary['unique_reviewed_images']}")
    print(f"Discrepancias criticas/correcoes: {summary['critical_discrepancies']}")
    print(f"BBox errada: {summary['wrong_bbox']}")
    print(f"Incertos: {summary['uncertain']}")
    print(f"Relatorio: {report_dir / 'visual_review_comparison.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
