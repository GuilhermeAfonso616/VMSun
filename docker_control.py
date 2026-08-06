import os
import sys
import ipaddress
import socket
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox


def detect_primary_lan_ipv4():
    """Descobre o IPv4 que o host usa para sair da maquina."""
    candidates = []
    for target in ("1.1.1.1", "8.8.8.8"):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((target, 80))
            candidates.append(probe.getsockname()[0])
        except OSError:
            pass
        finally:
            probe.close()

    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass

    for value in candidates:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4 and not address.is_loopback and not address.is_link_local:
            return str(address)
    return ""


def compose_environment():
    environment = {}
    env_path = os.path.join(get_working_dir(), ".env.docker")
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8-sig") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                environment[key.strip()] = value
    environment.update(os.environ)
    configured = str(environment.get("MTX_WEBRTCADDITIONALHOSTS") or "").strip()
    if not configured:
        detected = detect_primary_lan_ipv4()
        if detected:
            environment["MTX_WEBRTCADDITIONALHOSTS"] = detected
    return environment

def get_working_dir():
    # Prioritizes the user's specific Analitico directory if it exists and has the compose file
    default_path = r"D:\Analitico"
    if os.path.isdir(default_path) and os.path.isfile(os.path.join(default_path, "docker-compose.yml")):
        return default_path

    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
    curr = os.path.abspath(base_dir)
    for _ in range(4):
        if os.path.isfile(os.path.join(curr, "docker-compose.yml")):
            return curr
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent
    return base_dir

# Configuration of commands
COMPOSE_FILES = "--env-file .env.docker -f docker-compose.yml -f docker-compose.gpu.yml"
CMD_UP = f"docker compose {COMPOSE_FILES} up -d"
CMD_DOWN = f"docker compose {COMPOSE_FILES} down"
CMD_RESTART = f"docker compose {COMPOSE_FILES} up -d --force-recreate"
CMD_REBUILD = f"docker compose {COMPOSE_FILES} build analitico camera-gateway && docker compose {COMPOSE_FILES} up -d"

class DockerControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Analitico Docker Control")
        
        # Borderless window settings
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)
        
        # Color Palette (Slate Dark Theme)
        self.bg_main = "#0f172a"       # slate-900
        self.bg_card = "#1e293b"       # slate-800
        self.bg_terminal = "#020617"   # slate-950
        self.fg_main = "#cbd5e1"       # slate-300
        self.fg_active = "#f8fafc"     # slate-50
        self.color_play = "#10b981"    # emerald-500
        self.color_pause = "#ef4444"   # red-500
        self.color_restart = "#3b82f6" # blue-500
        self.color_rebuild = "#8b5cf6" # purple-500
        
        self.root.config(bg=self.bg_main, highlightbackground="#334155", highlightthickness=1)
        
        self.logs_expanded = False
        self.in_flight = False
        
        # Main drag variables
        self.drag_x = 0
        self.drag_y = 0
        
        self.build_ui()
        self.set_geometry()
        
        # Focus events
        self.root.bind("<FocusIn>", self.on_focus_in)
        self.root.bind("<FocusOut>", self.on_focus_out)
        
        # Status monitoring loop
        self.update_status_loop()
        
    def set_geometry(self):
        # Center or place in bottom-right corner of screen by default
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        w = 380
        h = 85 # height to comfortably hold buttons + console toggle
        # Place 40 pixels from bottom right
        x = screen_width - w - 40
        y = screen_height - h - 80
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        
    def build_ui(self):
        # Custom Header (Title and Drag Bar)
        self.header = tk.Frame(self.root, bg=self.bg_main, height=22)
        self.header.pack(fill="x", side="top")
        self.header.pack_propagate(False)
        
        # Drag binds
        self.header.bind("<Button-1>", self.start_drag)
        self.header.bind("<B1-Motion>", self.on_drag)
        
        # Icon Label (⚡)
        self.icon_label = tk.Label(self.header, text="⚡", bg=self.bg_main, fg="#94a3b8", font=("Segoe UI", 9, "bold"))
        self.icon_label.pack(side="left", padx=(8, 2))
        self.icon_label.bind("<Button-1>", self.start_drag)
        self.icon_label.bind("<B1-Motion>", self.on_drag)
        
        # Title Label
        self.title_label = tk.Label(self.header, text="Analitico Docker - Carregando...", bg=self.bg_main, fg="#cbd5e1", font=("Segoe UI", 9, "bold"))
        self.title_label.pack(side="left")
        self.title_label.bind("<Button-1>", self.start_drag)
        self.title_label.bind("<B1-Motion>", self.on_drag)
        
        # Minimize and Close buttons
        btn_close = tk.Button(self.header, text="✕", bg=self.bg_main, fg="#64748b", activebackground="#ef4444", activeforeground="#ffffff", bd=0, font=("Segoe UI", 8), command=self.exit_app, width=3, height=1)
        btn_close.pack(side="right")
        
        btn_min = tk.Button(self.header, text="—", bg=self.bg_main, fg="#64748b", activebackground=self.bg_card, activeforeground="#ffffff", bd=0, font=("Segoe UI", 8), command=self.minimize_app, width=3, height=1)
        btn_min.pack(side="right")
        
        # Toolbar Frame for Buttons
        self.toolbar = tk.Frame(self.root, bg=self.bg_main)
        self.toolbar.pack(fill="x", padx=5, pady=2)
        
        # Styled Flat Buttons
        self.btn_play = self.create_btn(self.toolbar, "▶ Play", self.color_play, lambda: self.execute("PLAY", CMD_UP))
        self.btn_play.pack(side="left", padx=3, fill="both", expand=True)
        
        self.btn_pause = self.create_btn(self.toolbar, "⏸ Pause", self.color_pause, lambda: self.execute("PAUSE", CMD_DOWN))
        self.btn_pause.pack(side="left", padx=3, fill="both", expand=True)
        
        self.btn_restart = self.create_btn(self.toolbar, "🔄 Restart", self.color_restart, lambda: self.execute("RESTART", CMD_RESTART))
        self.btn_restart.pack(side="left", padx=3, fill="both", expand=True)
        
        self.btn_rebuild = self.create_btn(self.toolbar, "🛠 Rebuild", self.color_rebuild, lambda: self.execute("REBUILD", CMD_REBUILD))
        self.btn_rebuild.pack(side="left", padx=3, fill="both", expand=True)
        
        # Console/Log toggle button
        self.toggle_btn = tk.Button(self.root, text="▼ Mostrar Console", bg=self.bg_main, fg="#64748b", activebackground=self.bg_main, activeforeground=self.fg_active, bd=0, font=("Segoe UI", 7, "bold"), cursor="hand2", command=self.toggle_logs)
        self.toggle_btn.pack(side="top", pady=1)
        
        # Log Terminal Frame (hidden initially)
        self.log_frame = tk.Frame(self.root, bg=self.bg_terminal)
        
        self.log_text = tk.Text(self.log_frame, bg=self.bg_terminal, fg="#10b981", font=("Consolas", 8), bd=0, wrap="word", highlightthickness=0)
        self.log_text.pack(fill="both", expand=True, side="left", padx=3, pady=3)
        
        self.scrollbar = tk.Scrollbar(self.log_frame, command=self.log_text.yview, bg=self.bg_terminal)
        self.scrollbar.pack(fill="y", side="right")
        self.log_text.config(yscrollcommand=self.scrollbar.set)
        
    def create_btn(self, parent, text, color, command):
        btn = tk.Button(
            parent,
            text=text,
            bg=self.bg_card,
            fg=color,
            activebackground=color,
            activeforeground=self.bg_main,
            bd=0,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            padx=2,
            pady=4,
            command=command
        )
        
        # Hover animations
        def on_enter(e):
            if not self.in_flight:
                btn.config(bg=self.bg_card, fg="#ffffff")
        def on_leave(e):
            if not self.in_flight:
                btn.config(bg=self.bg_card, fg=color)
                
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        return btn
        
    def toggle_logs(self):
        self.logs_expanded = not self.logs_expanded
        w = 520 if self.logs_expanded else 380
        h = 360 if self.logs_expanded else 85
        
        # Get current window position
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        
        if self.logs_expanded:
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            self.log_frame.pack(fill="both", expand=True, padx=5, pady=5)
            self.toggle_btn.config(text="▲ Ocultar Console")
        else:
            self.log_frame.pack_forget()
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            self.toggle_btn.config(text="▼ Mostrar Console")
            
    def start_drag(self, event):
        self.drag_x = event.x
        self.drag_y = event.y
        
    def on_drag(self, event):
        deltax = event.x - self.drag_x
        deltay = event.y - self.drag_y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")
        
    def minimize_app(self):
        self.root.iconify()
        
    def exit_app(self):
        if self.in_flight:
            if not messagebox.askyesno("Confirmar", "Um comando está rodando. Deseja realmente fechar o controle?"):
                return
        self.root.destroy()

    def on_focus_in(self, event):
        self.root.attributes("-alpha", 1.0)
        
    def on_focus_out(self, event):
        self.root.after(100, self._check_focus_loss)
        
    def _check_focus_loss(self):
        try:
            focused = self.root.focus_get()
            if focused is None:
                self.root.attributes("-alpha", 0.85)
                if self.logs_expanded:
                    self.toggle_logs()
        except Exception:
            pass

    def update_status_loop(self):
        if not self.in_flight:
            threading.Thread(target=self._check_docker_status, daemon=True).start()
        self.root.after(3000, self.update_status_loop)

    def _check_docker_status(self):
        try:
            cmd = f'docker compose {COMPOSE_FILES} ps --services --filter "status=running"'
            result = subprocess.run(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=get_working_dir()
            )
            running_services = [s.strip() for s in result.stdout.split("\n") if s.strip()]
            has_analitico = "analitico" in running_services
            has_gateway = "camera-gateway" in running_services
            
            if has_analitico and has_gateway:
                self.set_status_ui("Rodando", self.color_play)
            elif has_analitico or has_gateway:
                self.set_status_ui("Parcial", "#f59e0b")
            else:
                self.set_status_ui("Pausado", self.color_pause)
        except Exception:
            self.set_status_ui("Erro", self.color_pause)
            
    def set_status_ui(self, status, color):
        self.root.after(0, lambda: self._apply_status_ui(status, color))
        
    def _apply_status_ui(self, status, color):
        self.title_label.config(text=f"Analitico Docker - {status}", fg="#cbd5e1")
        self.icon_label.config(fg=color)
        
    def append_log(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert("end", message)
        self.log_text.see("end")
        self.log_text.config(state="disabled")
        
    def set_buttons_state(self, state):
        self.in_flight = (state == "disabled")
        for btn in (self.btn_play, self.btn_pause, self.btn_restart, self.btn_rebuild):
            btn.config(state=state)
            if state == "disabled":
                btn.config(bg="#334155", fg="#64748b", cursor="arrow")
            else:
                # restore original styles
                if btn == self.btn_play: btn.config(bg=self.bg_card, fg=self.color_play, cursor="hand2")
                elif btn == self.btn_pause: btn.config(bg=self.bg_card, fg=self.color_pause, cursor="hand2")
                elif btn == self.btn_restart: btn.config(bg=self.bg_card, fg=self.color_restart, cursor="hand2")
                elif btn == self.btn_rebuild: btn.config(bg=self.bg_card, fg=self.color_rebuild, cursor="hand2")
                
    def execute(self, name, command):
        if self.in_flight:
            return
            
        # Automatically open logs when a command runs
        if not self.logs_expanded:
            self.toggle_logs()
            
        self.set_buttons_state("disabled")
        
        # Set transition status
        if name == "PLAY":
            self._apply_status_ui("Iniciando...", self.color_play)
        elif name == "PAUSE":
            self._apply_status_ui("Pausando...", self.color_pause)
        elif name == "RESTART":
            self._apply_status_ui("Reiniciando...", self.color_restart)
        elif name == "REBUILD":
            self._apply_status_ui("Compilando...", self.color_rebuild)
            
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")
        
        self.append_log(f"⚡ [INICIANDO] {name}...\n")
        self.append_log(f"💻 Comando: {command}\n")
        self.append_log("—" * 60 + "\n")
        
        def worker():
            try:
                process_environment = compose_environment() if name != "PAUSE" else dict(os.environ)
                announced_hosts = process_environment.get("MTX_WEBRTCADDITIONALHOSTS", "")
                if announced_hosts and name != "PAUSE":
                    self.append_log(f"WebRTC LAN detectado: {announced_hosts}\n")
                # Run the command with real-time stream output
                process = subprocess.Popen(
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=get_working_dir(),
                    env=process_environment,
                )
                
                # Stream logs line-by-line
                for line in iter(process.stdout.readline, ""):
                    self.append_log(line)
                    
                process.stdout.close()
                return_code = process.wait()
                
                self.append_log("—" * 60 + "\n")
                if return_code == 0:
                    self.append_log(f"✅ [SUCESSO] Operação {name} concluída com sucesso!\n")
                else:
                    self.append_log(f"❌ [FALHA] Operação {name} retornou código de erro: {return_code}\n")
            except Exception as exc:
                self.append_log(f"💥 [EXCEÇÃO] Erro ao executar: {str(exc)}\n")
            finally:
                self.root.after(0, lambda: self.set_buttons_state("normal"))
                self.root.after(500, self._check_docker_status)
                
        threading.Thread(target=worker, daemon=True).start()

if __name__ == "__main__":
    root = tk.Tk()
    app = DockerControlApp(root)
    root.mainloop()
