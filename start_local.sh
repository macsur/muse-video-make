#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# 优先选择高兼容性的 Python 解释器（避免部分实验版本 pip 损坏问题）
PY_BIN=""
for cmd in python3.12 python3.11 python3.10 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.12 /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        if "$cmd" -c "import sys; exit(0 if sys.version_info >= (3, 8) else 1)" 2>/dev/null; then
            PY_BIN="$cmd"
            break
        fi
    fi
done

if [ -z "$PY_BIN" ]; then
    echo "❌ 未检测到可用的 Python 3.8+ 环境，请先安装 Python (https://www.python.org/)"
    exit 1
fi

chmod +x run_local.py tools/get_muse_cookie.py 2>/dev/null || true
exec "$PY_BIN" run_local.py "$@"
