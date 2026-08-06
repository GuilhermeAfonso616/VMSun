import os
import re
import subprocess
import glob

def main():
    # Scan dist directory for existing executables
    dist_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")
    os.makedirs(dist_dir, exist_ok=True)
    
    existing_exes = glob.glob(os.path.join(dist_dir, "AnaliticoDockerControl_v*.exe"))
    
    max_version = 0
    for exe in existing_exes:
        name = os.path.basename(exe)
        match = re.search(r"_v(\d+)\.exe$", name)
        if match:
            max_version = max(max_version, int(match.group(1)))
            
    next_version = max_version + 1
    new_name = f"AnaliticoDockerControl_v{next_version}"
    
    print(f"[BUILD] Compilando a versao: {new_name}.exe")
    
    cmd = f'pyinstaller --onefile --noconsole --name "{new_name}" docker_control.py'
    
    result = subprocess.run(cmd, shell=True)
    if result.returncode == 0:
        print(f"[SUCCESS] Build completo! Executavel disponivel em: dist/{new_name}.exe")
    else:
        print(f"[ERROR] Falha ao compilar {new_name}.exe")

if __name__ == "__main__":
    main()
