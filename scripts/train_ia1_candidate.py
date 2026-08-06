import argparse
import shutil
from pathlib import Path

import torch
import yaml
from ultralytics import YOLO


def load_config(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def train(config):
    dataset_yaml = Path(config["paths"]["output_dataset"]) / "data.yaml"
    base_model = Path(config["paths"]["ia1_current_model"])
    if not dataset_yaml.exists():
        raise FileNotFoundError(f"Dataset YAML not found: {dataset_yaml}")
    if not base_model.exists():
        raise FileNotFoundError(f"Current IA1 model not found: {base_model}")

    train_cfg = config["training"]
    device = 0 if torch.cuda.is_available() else "cpu"
    batch = int(train_cfg["batch"]) if torch.cuda.is_available() else min(4, int(train_cfg["batch"]))

    model = YOLO(str(base_model))
    run_name = config.get("training", {}).get("run_name") or config.get("name", "IA1_candidate_vms_hardneg_v1")
    model.train(
        data=str(dataset_yaml),
        epochs=int(train_cfg["epochs"]),
        imgsz=int(train_cfg["imgsz"]),
        batch=batch,
        device=device,
        workers=int(train_cfg.get("workers", 2)),
        optimizer=train_cfg.get("optimizer", "AdamW"),
        lr0=float(train_cfg.get("lr0", 5e-5)),
        lrf=float(train_cfg.get("lrf", 0.01)),
        patience=int(train_cfg.get("patience", 20)),
        project=str(Path(config["paths"]["runs"])),
        name=run_name,
        seed=int(train_cfg.get("seed", 43)),
        pretrained=True,
        cache=False,
        plots=True,
        save=True,
    )
    best = Path(model.trainer.save_dir) / "weights" / "best.pt"
    target = Path(config["paths"]["ia1_candidate_model"])
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, target)
    print("run:", model.trainer.save_dir)
    print("candidate:", target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/ia1_finetune_vms_hardneg_v1.yaml")
    args = parser.parse_args()
    train(load_config(args.config))


if __name__ == "__main__":
    main()
