#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
if [ ! -x .venv/bin/python ]; then
  echo '请先安装 Python 3.12，再运行 安装.command。'
  exit 1
fi
exec .venv/bin/python launch.pyw
