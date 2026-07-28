"""
Refresh completo do painel: Pipefy → Sapron → HTML.
Logs em refresh.log; exit code != 0 se algo falhar.
"""
import os, subprocess, sys, time, traceback
from pathlib import Path
from datetime import datetime

# stdout/stderr filhos em UTF-8 (evita erro cp1252 no Windows)
CHILD_ENV = os.environ.copy()
CHILD_ENV['PYTHONIOENCODING'] = 'utf-8'
CHILD_ENV['PYTHONUTF8'] = '1'

BASE = Path(__file__).parent
LOG = BASE / 'refresh.log'

def log(msg):
    line = f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] {msg}'
    print(line)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(line + '\n')

def run_step(name, script):
    log(f'--- {name} ---')
    t0 = time.time()
    r = subprocess.run([sys.executable, str(BASE / script)],
                       capture_output=True, text=True, encoding='utf-8',
                       env=CHILD_ENV)
    dt = time.time() - t0
    if r.stdout: log(f'stdout: {r.stdout.strip()}')
    if r.stderr: log(f'stderr: {r.stderr.strip()}')
    log(f'{name} exit={r.returncode} ({dt:.1f}s)')
    if r.returncode != 0:
        raise RuntimeError(f'{name} falhou')

def main():
    log('===== refresh start =====')
    try:
        run_step('step1 Pipefy',   'step1_pipefy.py')
        run_step('step2 Sapron',   'step2_sapron.py')
        run_step('step3 publish',  'step3_publish.py')
        log('===== refresh OK =====')
        return 0
    except Exception:
        log('ERRO: ' + traceback.format_exc())
        return 1

if __name__ == '__main__':
    sys.exit(main())
