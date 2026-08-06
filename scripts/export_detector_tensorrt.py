"""Exporta o detector IA1 (.pt) para TensorRT (.engine) ou ONNX (.onnx).

Ganho tipico: 1.5-3x de throughput em GPU NVIDIA, MANTENDO a resolucao de
inferencia (1024) — ou seja, sem perder pixel/recall. O engine TensorRT e
especifico da GPU e da versao do TensorRT: gere-o NA MESMA maquina/GPU onde o
servidor vai rodar (ou identica).

Uso (dentro do container de runtime, que tem CUDA/torch/ultralytics):

    python scripts/export_detector_tensorrt.py \
        --weights /models/ia1_candidate/ia1_candidate_vms_hardneg_v3_2_1024.pt \
        --imgsz 1024 --format engine --half

Depois, aponte a env e reinicie o runtime:

    DETECTOR_ENGINE_PATH=/models/ia1_candidate/ia1_candidate_vms_hardneg_v3_2_1024.engine

O detector carrega o engine automaticamente quando o arquivo existe; se nao
existir, faz fallback seguro para o .pt (ver app/analytics/detector.py).

Observacoes:
- imgsz DEVE bater com settings.detect_imgsz (1024). Um engine 1024 nao serve
  para inferir em outra resolucao.
- O engine e construido com batch=1 (uma camera por chamada), que e o modo
  atual do worker. Se um dia houver batching, reexporte com o batch alvo.
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Exporta detector IA1 para TensorRT/ONNX.")
    parser.add_argument("--weights", required=True, help="Caminho do .pt de origem.")
    parser.add_argument("--imgsz", type=int, default=1024, help="Resolucao do engine (default 1024).")
    parser.add_argument(
        "--format",
        choices=["engine", "onnx"],
        default="engine",
        help="engine=TensorRT (mais rapido, especifico da GPU); onnx=portavel.",
    )
    parser.add_argument("--half", action="store_true", help="Precisao FP16 (recomendado em GPU).")
    parser.add_argument("--device", default="0", help="Device CUDA para o export (default 0).")
    parser.add_argument("--workspace", type=int, default=4, help="Workspace TensorRT em GB.")
    args = parser.parse_args()

    if not os.path.exists(args.weights):
        print(f"[erro] pesos nao encontrados: {args.weights}", file=sys.stderr)
        return 2

    try:
        from ultralytics import YOLO
    except Exception as exc:  # pragma: no cover - depende do ambiente CUDA
        print(f"[erro] ultralytics indisponivel: {exc}", file=sys.stderr)
        return 3

    print(f"[info] carregando {args.weights}")
    model = YOLO(args.weights)

    export_kwargs = dict(
        format=args.format,
        imgsz=args.imgsz,
        half=args.half,
        device=args.device,
        verbose=True,
    )
    if args.format == "engine":
        export_kwargs["workspace"] = args.workspace

    print(f"[info] exportando format={args.format} imgsz={args.imgsz} half={args.half} device={args.device}")
    exported_path = model.export(**export_kwargs)

    print(f"[ok] artefato gerado: {exported_path}")
    print("[proximo passo] defina DETECTOR_ENGINE_PATH apontando para esse arquivo e reinicie o runtime.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
