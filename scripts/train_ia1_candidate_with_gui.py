import argparse
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, TOP, Button, Frame, Label, StringVar, Text, Tk
from tkinter.ttk import Progressbar

import yaml


DEFAULT_CONFIG = Path("D:/Analitico/configs/ia1_finetune_vms_hardneg_v2.yaml")


EPOCH_RE = re.compile(r"^\s*(\d+)/(\d+)\s+")


def parse_args():
    parser = argparse.ArgumentParser(description="Executa build, treino e avaliacao da IA1 candidate com interface.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Config YAML.")
    parser.add_argument("--skip-build", action="store_true", help="Nao reconstrui o dataset.")
    parser.add_argument("--skip-eval", action="store_true", help="Nao roda avaliacao depois do treino.")
    parser.add_argument("--auto-start", action="store_true", help="Inicia automaticamente ao abrir a interface.")
    return parser.parse_args()


def load_config(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TrainingGui:
    def __init__(self, args):
        self.args = args
        self.config_path = Path(args.config)
        self.config = load_config(self.config_path)
        self.project_root = Path(self.config["paths"]["project_root"])
        self.total_epochs = int(self.config["training"]["epochs"])
        self.current_process = None
        self.log_queue = queue.Queue()
        self.stop_requested = False

        self.root = Tk()
        self.root.title(f"Treino IA1 candidate - {self.config.get('name', '')}")
        self.root.geometry("1120x780")

        self.status = StringVar(value="Pronto para iniciar")
        self.stage = StringVar(value="Etapa: aguardando")
        self.epoch = StringVar(value=f"Epoca: 0/{self.total_epochs}")
        self.dataset = StringVar(value=f"Dataset: {self.config['paths']['output_dataset']}")
        self.model = StringVar(value=f"Modelo destino: {self.config['paths']['ia1_candidate_model']}")

        Label(self.root, textvariable=self.status, anchor="w").pack(side=TOP, fill="x")
        Label(self.root, textvariable=self.stage, anchor="w").pack(side=TOP, fill="x")
        Label(self.root, textvariable=self.epoch, anchor="w").pack(side=TOP, fill="x")
        Label(self.root, textvariable=self.dataset, anchor="w").pack(side=TOP, fill="x")
        Label(self.root, textvariable=self.model, anchor="w").pack(side=TOP, fill="x")

        self.stage_progress = Progressbar(self.root, maximum=100)
        self.stage_progress.pack(side=TOP, fill="x", padx=6, pady=4)
        self.epoch_progress = Progressbar(self.root, maximum=100)
        self.epoch_progress.pack(side=TOP, fill="x", padx=6, pady=4)

        self.log = Text(self.root, wrap="word", height=32)
        self.log.pack(side=TOP, fill=BOTH, expand=True, padx=6, pady=4)

        buttons = Frame(self.root)
        buttons.pack(side=TOP, fill="x")
        self.start_button = Button(buttons, text="Iniciar", command=self.start)
        self.start_button.pack(side=LEFT, padx=4, pady=6)
        Button(buttons, text="Parar", command=self.stop).pack(side=LEFT, padx=4, pady=6)
        Button(buttons, text="Sair", command=self.root.destroy).pack(side=RIGHT, padx=4, pady=6)

        self.root.after(150, self.drain_queue)
        if args.auto_start:
            self.root.after(500, self.start)

    def append_log(self, text):
        self.log.insert(END, text)
        self.log.see(END)

    def start(self):
        self.start_button.configure(state="disabled")
        thread = threading.Thread(target=self.run_pipeline, daemon=True)
        thread.start()

    def stop(self):
        self.stop_requested = True
        if self.current_process and self.current_process.poll() is None:
            self.current_process.terminate()
        self.status.set("Parada solicitada")

    def drain_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.append_log(item[1])
                    self.update_epoch_from_line(item[1])
                elif kind == "status":
                    self.status.set(item[1])
                elif kind == "stage":
                    self.stage.set(item[1])
                    self.stage_progress["value"] = item[2]
                elif kind == "done":
                    self.status.set(item[1])
                    self.stage_progress["value"] = 100
                    self.epoch_progress["value"] = 100
                    self.start_button.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(150, self.drain_queue)

    def update_epoch_from_line(self, line):
        match = EPOCH_RE.match(line)
        if not match:
            return
        current = int(match.group(1))
        total = int(match.group(2))
        pct = min(100, max(0, current / max(total, 1) * 100))
        self.epoch.set(f"Epoca: {current}/{total} ({pct:.1f}%)")
        self.epoch_progress["value"] = pct

    def run_command(self, title, command, stage_pct):
        if self.stop_requested:
            raise RuntimeError("Execucao interrompida pelo usuario.")
        self.log_queue.put(("stage", f"Etapa: {title}", stage_pct))
        self.log_queue.put(("status", f"Rodando: {title}"))
        self.log_queue.put(("log", "\n" + "=" * 80 + "\n"))
        self.log_queue.put(("log", f"{title}\n"))
        self.log_queue.put(("log", " ".join(command) + "\n"))

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self.current_process = subprocess.Popen(
            command,
            cwd=str(self.project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        assert self.current_process.stdout is not None
        for line in self.current_process.stdout:
            self.log_queue.put(("log", line))
        code = self.current_process.wait()
        if code != 0:
            raise RuntimeError(f"Comando falhou ({code}): {' '.join(command)}")

    def run_pipeline(self):
        try:
            py = sys.executable
            config = str(self.config_path)
            if not self.args.skip_build:
                self.run_command("1/3 Construindo dataset V2", [py, "scripts/build_ia1_finetune_dataset.py", "--config", config], 10)
            self.run_command("2/3 Treinando IA1 candidate V2", [py, "scripts/train_ia1_candidate.py", "--config", config], 35)
            if not self.args.skip_eval:
                self.run_command("3/3 Avaliando candidate V2", [py, "scripts/evaluate_ia1_candidate.py", "--config", config], 85)
            self.log_queue.put(("done", "Concluido. Revise os relatorios antes de qualquer uso operacional."))
        except Exception as exc:
            self.log_queue.put(("log", f"\nERRO: {exc}\n"))
            self.log_queue.put(("done", f"Falhou: {exc}"))

    def run(self):
        self.root.mainloop()


def main():
    args = parse_args()
    TrainingGui(args).run()


if __name__ == "__main__":
    main()
