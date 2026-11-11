"""VS Code entry point: select the project environment and run an automatic batch."""
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
MODULE = 'localization_agent.workflow.cli'


def main():
    if not PYTHON.exists():
        raise SystemExit('项目环境不存在。请按 START_HERE.md 创建 .venv 并安装依赖。')
    # sys.prefix distinguishes the venv even when its Python executable is a symlink.
    if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
        os.execv(str(PYTHON), [str(PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
    os.chdir(ROOT)
    if len(sys.argv) > 1:
        os.execv(str(PYTHON), [str(PYTHON), '-m', MODULE, *sys.argv[1:]])
    stamp = datetime.now(ZoneInfo('Asia/Hong_Kong')).strftime('%Y%m%d-%H%M%S')
    folder = ROOT / 'outputs' / ('deepseek-autonomous-' + stamp)
    print('开始自主翻译、审查与有限修复：', folder, flush=True)
    subprocess.run([str(PYTHON), '-m', MODULE, 'init', '--directory', str(folder)], check=True)
    os.execv(str(PYTHON), [str(PYTHON), '-m', MODULE, 'run', '--directory', str(folder)])


if __name__ == '__main__':
    main()
