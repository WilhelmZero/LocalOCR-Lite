"""Install just the UI; OCR dependencies and weights are user-selected later."""
import json
import platform
import subprocess
import sys
from pathlib import Path

def main():
    if sys.version_info[:2]!=(3,12):
        raise SystemExit('Please install Python 3.12, then run this installer again.')
    root=Path(__file__).resolve().parents[1]
    runtime=root/'.venv'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    if not runtime.exists():
        subprocess.run([sys.executable,'-m','venv',str(root/'.venv')],check=True)
    subprocess.run([str(runtime),'-m','pip','install','-r',str(root/'requirements-ui.lock.txt')],check=True)
    subprocess.run([str(runtime),'-m','pip','check'],check=True)
    data=root/'data';data.mkdir(exist_ok=True)
    with (data/'installed-ui.txt').open('w',encoding='utf-8') as f:
        subprocess.run([str(runtime),'-m','pip','freeze'],stdout=f,check=True)
    (data/'platform.json').write_text(json.dumps({'platform':platform.platform(),'machine':platform.machine(),'python':str(runtime)},indent=2))
    print('UI installed. Launch the app and choose models to download. No OCR weights installed.')

if __name__=='__main__':main()
