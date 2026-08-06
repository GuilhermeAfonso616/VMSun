r"""Mescla dois ou mais pacotes de dataset de treino do revalidador
(formato `collect_revalidator_training_dataset.py`: `<class>/{crops_ia2,
crops_ia3_far,context,metadata}` + `events.csv` + `manifest.json`) em um
unico pacote combinado.

Usado para juntar o dataset de maio/2026 (cameras de teste 7-25, em
`D:\IA2\revalidator\datasets\raw\...`) com o dataset de julho/2026 (cameras
reais de producao 40-67, gerado por
`collect_revalidator_training_dataset_from_export.py`). Os intervalos de
camera_id das duas fontes nao se cruzam, entao a copia e so por nome de
arquivo (ja unico por incluir `cam<id>_track<id>`), sem necessidade de
deduplicar por conteudo aqui - isso fica pro proprio script de build
(`build_ia2_v8c_dataset.py` ja deduplica por sha256 no proximo passo).

Exemplo:

    python -B scripts/merge_revalidator_datasets.py ^
        --source "D:\IA2\revalidator\datasets\raw\revalidator_training_dataset_20260511_122824=may_2026_test_cameras" ^
        --source "D:\Analitico\reports\revalidator_training\revalidator_training_dataset_from_export_20260721_134815=july_2026_production_export" ^
        --out "D:\IA2\revalidator\datasets\raw\merged_2026-07_may_test_plus_july_production"
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CLASS_NAMES = ("person", "not_person", "uncertain", "unlabeled")
CROP_SUBDIRS = ("crops_ia2", "crops_ia3_far", "context", "metadata")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mescla pacotes de dataset do revalidador em um unico pacote.")
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        dest="sources",
        help="Pasta do pacote de origem, no formato 'caminho=tag_da_fonte'. Pode repetir.",
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--clean", action="store_true", help="Remove --out antes de mesclar, se existir.")
    return parser.parse_args()


def parse_source(value: str) -> tuple[Path, str]:
    if "=" not in value:
        raise SystemExit(f"--source invalido (esperado caminho=tag): {value}")
    path_str, tag = value.split("=", 1)
    path = Path(path_str.strip())
    if not path.exists():
        raise SystemExit(f"Pasta de origem nao encontrada: {path}")
    return path, tag.strip() or path.name


def ensure_out_dirs(out: Path) -> None:
    for class_name in CLASS_NAMES:
        for sub in CROP_SUBDIRS:
            (out / class_name / sub).mkdir(parents=True, exist_ok=True)


def copy_class_files(source_root: Path, out_root: Path, *, source_tag: str) -> Counter:
    counts: Counter = Counter()
    for class_name in CLASS_NAMES:
        for sub in CROP_SUBDIRS:
            src_dir = source_root / class_name / sub
            if not src_dir.exists():
                continue
            dst_dir = out_root / class_name / sub
            for src_file in src_dir.iterdir():
                if not src_file.is_file():
                    continue
                dst_file = dst_dir / src_file.name
                if dst_file.exists():
                    counts[(class_name, sub, "skipped_name_collision")] += 1
                    continue
                shutil.copy2(src_file, dst_file)
                counts[(class_name, sub, "copied")] += 1
    return counts


def read_events_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_combined_csv(out_path: Path, rows: list[dict[str, Any]], *, fieldnames: list[str]) -> None:
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    sources = [parse_source(value) for value in args.sources]

    out = Path(args.out).resolve()
    if args.clean and out.exists():
        shutil.rmtree(out)
    ensure_out_dirs(out)

    copy_counts: Counter = Counter()
    combined_rows: list[dict[str, Any]] = []
    fieldnames: list[str] = []

    for source_root, source_tag in sources:
        counts = copy_class_files(source_root, out, source_tag=source_tag)
        copy_counts.update(counts)

        rows = read_events_csv(source_root / "events.csv")
        for row in rows:
            row["source_dataset"] = row.get("source_dataset") or source_tag
        combined_rows.extend(rows)
        for row in rows:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(key)

        print(f"fonte '{source_tag}' ({source_root}): {len(rows)} linhas em events.csv")

    write_combined_csv(out / "events.csv", combined_rows, fieldnames=fieldnames)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": [{"path": str(root), "tag": tag} for root, tag in sources],
        "total_events_csv_rows": len(combined_rows),
        "counts_by_class_in_events_csv": dict(Counter(row.get("class_name") or "unknown" for row in combined_rows)),
        "counts_by_camera_in_events_csv": dict(Counter(row.get("camera_id") or "unknown" for row in combined_rows)),
        "counts_by_source_in_events_csv": dict(Counter(row.get("source_dataset") or "unknown" for row in combined_rows)),
        "file_copy_counts": {"|".join(map(str, key)): value for key, value in copy_counts.items()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nResumo da copia de arquivos (class, subpasta, status):")
    for key, value in sorted(copy_counts.items()):
        print(f"  {key}: {value}")

    print(f"\nSaida: {out}")
    print(f"events.csv combinado: {len(combined_rows)} linhas")
    print("Por classe:", manifest["counts_by_class_in_events_csv"])
    print("Por fonte:", manifest["counts_by_source_in_events_csv"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
