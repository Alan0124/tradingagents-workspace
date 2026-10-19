#!/bin/zsh
cd -- "${0:A:h}" || exit 1
./.venv/bin/tradingagents "$@"
run_status=$?
if (( run_status != 0 )); then
  printf '\n启动结束，退出码：%s。按回车关闭窗口。\n' "$run_status"
  read -r
fi
exit "$run_status"
