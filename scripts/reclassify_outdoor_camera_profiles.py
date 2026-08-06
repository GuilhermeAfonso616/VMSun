r"""Reclassifica `scene_profile` das cameras externas/perimetrais que estao
presas no fallback `indoor_discreet` (item 4 do plano de reducao de falsos
positivos de `intrusion_default`).

Contexto: `profile_from_camera()` (`app/analytics/camera_profile_models.py:581-639`)
cai no fallback hardcoded `camera_family="dome", scene_profile="indoor_discreet"`
sempre que `camera.analytics_profile_json` esta vazio. Todos os 507 eventos
rotulados em `D:\IA_Rebuild\Analitico VMS Clips` mostram esse fallback, mesmo
para cameras confirmadas visualmente (overlay "Bullet", "Thermal", vista de
perimetro/cerca/campo aberto) como externas. `scene_profile="perimeter_outdoor"`
ja e lido de verdade por `_effective_threshold_profile()`
(`app/analytics/camera_policy_builder.py:213-218`): sobe
`person_confidence_min` (>=0.45), `track_persistence_frames` (>=4),
`alarm_confirmation_seconds` (>=1.2s) e `cooldown_seconds` (>=12s). Nao mexe em
ROI/exclusao - `roi_required`/`full_frame_forbidden` resultantes nao sao lidos
por nenhuma regra do motor de analise hoje (`intrusion_zone.py`,
`event_pipeline.py`).

So altera `scene_profile`. Mantem o resto do perfil (threshold_profile,
nuisance_profile, roi_polygon etc.) como estava, via merge com
`get_camera_profile()` - nao sobrescreve nada que ja tenha sido configurado
manualmente para essa camera.

Exemplos:

    python -B scripts/reclassify_outdoor_camera_profiles.py --dry-run
    python -B scripts/reclassify_outdoor_camera_profiles.py --camera-id 42 --camera-id 65
    python -B scripts/reclassify_outdoor_camera_profiles.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import settings, sqlite_url_for  # noqa: E402
from app.services.camera_configuration_service import (  # noqa: E402
    get_camera_profile,
    update_camera_profile,
)

# Cameras confirmadas visualmente (snapshots de D:\IA_Rebuild\Analitico VMS Clips)
# como externas/perimetrais - ver docs/plan/reduzir-falsos-positivos-intrusion-default.md.
DEFAULT_CAMERA_IDS = [40, 42, 43, 44, 45, 48, 55, 56, 58, 65]
TARGET_SCENE_PROFILE = "perimeter_outdoor"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reclassifica scene_profile de cameras externas.")
    parser.add_argument("--database", help="Caminho do analytics.db SQLite. Padrao: settings.database_url.")
    parser.add_argument(
        "--camera-id",
        type=int,
        action="append",
        dest="camera_ids",
        help="Camera a reclassificar. Pode repetir. Padrao: lista confirmada visualmente.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Mostra o que seria feito sem gravar no banco.")
    return parser.parse_args()


def database_url_from_args(database: str | None) -> str:
    if database:
        return sqlite_url_for(Path(database))
    return settings.database_url


def main() -> int:
    args = parse_args()
    camera_ids = args.camera_ids or DEFAULT_CAMERA_IDS

    database_url = database_url_from_args(args.database)
    print(f"Banco alvo: {database_url}")
    print(f"Cameras alvo: {camera_ids}")

    engine = create_engine(database_url, poolclass=NullPool)
    Session = sessionmaker(bind=engine)
    session = Session()

    changed = 0
    unchanged = 0
    missing = 0

    try:
        for camera_id in camera_ids:
            try:
                profile = get_camera_profile(session, camera_id)
            except Exception as exc:
                print(f"  camera_id={camera_id}: nao encontrada ({exc})")
                missing += 1
                continue

            before = profile.scene_profile
            if before == TARGET_SCENE_PROFILE:
                print(f"  camera_id={camera_id}: ja esta em '{TARGET_SCENE_PROFILE}', pulando")
                unchanged += 1
                continue

            print(
                f"  camera_id={camera_id}: scene_profile '{before}' -> '{TARGET_SCENE_PROFILE}' "
                f"(camera_family={profile.camera_family}, preset_name={profile.preset_name})"
            )
            changed += 1
            if args.dry_run:
                continue

            payload = profile.to_dict()
            payload["scene_profile"] = TARGET_SCENE_PROFILE
            update_camera_profile(session, camera_id, payload)

        if not args.dry_run:
            session.commit()
    finally:
        session.close()

    print(f"\nalterados={changed} ja_corretos={unchanged} nao_encontrados={missing} dry_run={bool(args.dry_run)}")
    if args.dry_run and changed:
        print("Rode sem --dry-run para aplicar de fato no banco.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
