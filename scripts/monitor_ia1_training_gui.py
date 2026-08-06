import argparse
import csv
from pathlib import Path
from tkinter import BOTH, END, TOP, Label, StringVar, Text, Tk
from tkinter.ttk import Progressbar

import yaml


DEFAULT_CONFIG = Path("D:/Analitico/configs/ia1_finetune_vms_hardneg_v2.yaml")


def parse_args():
    parser = argparse.ArgumentParser(description="Monitora treino IA1 candidate em uma interface simples.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Config YAML usado no treino.")
    parser.add_argument("--run-dir", default="", help="Opcional: pasta do run YOLO.")
    return parser.parse_args()


def load_config(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fmt_seconds(value):
    try:
        total = int(round(float(value)))
    except (TypeError, ValueError):
        return "-"
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class MonitorApp:
    def __init__(self, config_path, run_dir):
        self.config_path = Path(config_path)
        self.config = load_config(self.config_path)
        self.epochs = int(self.config["training"]["epochs"])
        self.run_dir = Path(run_dir) if run_dir else Path(self.config["paths"]["runs"]) / self.config["training"]["run_name"]
        self.results_csv = self.run_dir / "results.csv"
        self.model_path = Path(self.config["paths"]["ia1_candidate_model"])

        self.root = Tk()
        self.root.title("Monitor IA1 candidate V2")
        self.root.geometry("920x560")

        self.status = StringVar(value="Aguardando results.csv...")
        self.epoch_text = StringVar(value=f"Epoca: 0/{self.epochs}")
        self.metrics_text = StringVar(value="precision: - | recall: - | mAP50: - | mAP50-95: -")
        self.time_text = StringVar(value="tempo total: - | ultima epoca: - | media/epoca: - | ETA: -")
        self.paths_text = StringVar(value=f"Run: {self.run_dir}")
        self.model_text = StringVar(value=f"Modelo destino: {self.model_path}")

        Label(self.root, textvariable=self.status, anchor="w").pack(side=TOP, fill="x")
        Label(self.root, textvariable=self.epoch_text, anchor="w").pack(side=TOP, fill="x")
        Label(self.root, textvariable=self.metrics_text, anchor="w").pack(side=TOP, fill="x")
        Label(self.root, textvariable=self.time_text, anchor="w").pack(side=TOP, fill="x")
        Label(self.root, textvariable=self.paths_text, anchor="w").pack(side=TOP, fill="x")
        Label(self.root, textvariable=self.model_text, anchor="w").pack(side=TOP, fill="x")

        self.progress = Progressbar(self.root, maximum=100)
        self.progress.pack(side=TOP, fill="x", padx=8, pady=8)

        self.log = Text(self.root, wrap="none", height=22)
        self.log.pack(side=TOP, fill=BOTH, expand=True, padx=8, pady=8)

        self.root.after(1000, self.refresh)

    def read_rows(self):
        if not self.results_csv.exists():
            return []
        with self.results_csv.open("r", newline="", encoding="utf-8", errors="ignore") as f:
            return list(csv.DictReader(f))

    def refresh(self):
        try:
            rows = self.read_rows()
            weights_best = self.run_dir / "weights" / "best.pt"
            weights_last = self.run_dir / "weights" / "last.pt"
            if rows:
                last = rows[-1]
                epoch = int(float(last.get("epoch", "0")))
                pct = min(100.0, epoch / max(self.epochs, 1) * 100.0)
                precision = float(last.get("metrics/precision(B)", "0"))
                recall = float(last.get("metrics/recall(B)", "0"))
                map50 = float(last.get("metrics/mAP50(B)", "0"))
                map5095 = float(last.get("metrics/mAP50-95(B)", "0"))
                elapsed = float(last.get("time", "0") or 0)
                previous_elapsed = float(rows[-2].get("time", "0") or 0) if len(rows) >= 2 else 0.0
                last_epoch_time = elapsed - previous_elapsed if previous_elapsed > 0 else elapsed
                avg_epoch_time = elapsed / max(epoch, 1)
                eta = avg_epoch_time * max(self.epochs - epoch, 0)
                self.status.set("Treino em andamento" if not weights_last.exists() else "Pesos ja foram gravados; treino pode estar finalizando.")
                self.epoch_text.set(f"Epoca: {epoch}/{self.epochs} ({pct:.1f}%)")
                self.metrics_text.set(
                    f"precision: {precision:.4f} | recall: {recall:.4f} | mAP50: {map50:.4f} | mAP50-95: {map5095:.4f}"
                )
                self.time_text.set(
                    f"tempo total: {fmt_seconds(elapsed)} | ultima epoca: {fmt_seconds(last_epoch_time)} | "
                    f"media/epoca: {fmt_seconds(avg_epoch_time)} | ETA: {fmt_seconds(eta)}"
                )
                self.progress["value"] = pct
                self.log.delete("1.0", END)
                self.log.insert(END, self.results_csv.read_text(encoding="utf-8", errors="ignore"))
            else:
                self.status.set(f"Aguardando metricas em: {self.results_csv}")
            if self.model_path.exists() or weights_best.exists():
                self.status.set("Treino concluido ou best.pt disponivel. Pode rodar avaliacao quando o processo terminar.")
        except Exception as exc:
            self.status.set(f"Erro lendo progresso: {exc}")
        self.root.after(3000, self.refresh)

    def run(self):
        self.root.mainloop()


def main():
    args = parse_args()
    MonitorApp(args.config, args.run_dir).run()


if __name__ == "__main__":
    main()
