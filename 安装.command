#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
python3.12 tools/install_minimal.py
echo '安装完成，请双击 启动.command。'
