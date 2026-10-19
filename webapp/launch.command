#!/bin/zsh
cd -- "${0:A:h}/.." || exit 1
mkdir -p .local/web
nohup .venv/bin/python -m webapp.server --port 18765 --open > .local/web/server.log 2>&1 < /dev/null &
