#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  printf '%s\n' '未找到项目虚拟环境，请先安装 Python 3.11+（推荐 3.13），在项目目录执行：' \
    'python3.13 -m venv .venv' 'source .venv/bin/activate' 'python -m pip install -e .' >&2
  run_status=1
else
  ./.venv/bin/python -m webapp.server --open "$@"
  run_status=$?
fi
if (( run_status != 0 )) && [[ -t 0 ]]; then
  printf '\n启动结束，退出码：%s。按回车关闭窗口。\n' "$run_status"
  read -r
fi
exit "$run_status"
