#!/bin/bash
# Compatible with the Bash version bundled with macOS.
set -e
cd -- "$(dirname -- "$0")"

failed() {
    echo "启动失败，请查看上方错误。 / Startup failed. See the error above."
    if [[ -t 0 ]]; then
        read -r -p "按回车关闭窗口 / Press Return to close..." || true
    fi
    exit 1
}
trap failed ERR

if [[ ! -x .venv/bin/python ]]; then
    if [[ -e .venv/Scripts/python.exe ]]; then
        echo "检测到 Windows 虚拟环境，请先移走 .venv 后重试。"
        echo "Windows virtual environment detected. Move .venv elsewhere, then retry."
        failed
    fi
    python_cmd=""
    candidates=(
        python3 python
        /opt/homebrew/bin/python3
        /usr/local/bin/python3
        "$HOME/miniconda3/bin/python3"
        "$HOME/anaconda3/bin/python3"
        "$HOME/miniforge3/bin/python3"
        /opt/anaconda3/bin/python3
        /opt/miniconda3/bin/python3
        /Library/Frameworks/Python.framework/Versions/*/bin/python3
    )
    for candidate in "${candidates[@]}"; do
        if "$candidate" -c 'import sys, venv; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
            python_cmd="$candidate"
            break
        fi
    done
    if [[ -z "$python_cmd" ]]; then
        echo "未找到 Python 3.11 或更新版本，请安装后重试。"
        echo "No working Python 3.11+ found. Install it and retry."
        failed
    fi
    echo "创建虚拟环境 / Creating virtual environment: $python_cmd"
    "$python_cmd" -m venv .venv
fi

if ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "虚拟环境不可用，请移走 .venv 后重试。 / Move the unusable .venv elsewhere and retry."
    failed
fi
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python launch.py
