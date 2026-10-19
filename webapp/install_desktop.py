"""Generate a macOS desktop launcher for this Python environment."""

import json
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def launcher_script(workspace: Path, executable: str, log_directory: Path) -> str:
    """Detach the server, including its inherited descriptors, from AppleScript."""
    command = (
        f"cd {shlex.quote(str(workspace))} && "
        f"mkdir -p {shlex.quote(str(log_directory))} && "
        f"nohup env TRADINGAGENTS_WORKSPACE_DIR={shlex.quote(str(workspace))} "
        f"{shlex.quote(executable)} -m webapp.server --port 18765 --open "
        f"> {shlex.quote(str(log_directory / 'server.log'))} 2>&1 < /dev/null &"
    )
    # Close the background shell's own descriptors as well, so the app exits promptly.
    detached = f"/bin/sh -c {shlex.quote(command)} > /dev/null 2>&1 < /dev/null"
    return "on run\n  do shell script " + json.dumps(detached, ensure_ascii=False) + "\nend run\n"


def main():
    if sys.platform != "darwin":
        raise SystemExit("桌面 .app 启动器仅支持 macOS；其他系统请运行 tradingagents-web --open。")
    from webapp.core import DATA, ROOT

    target = Path.home() / "Desktop" / "TradingAgents 投研台.app"
    if target.exists():
        try:
            existing = plistlib.loads((target / "Contents/Info.plist").read_bytes())
        except (OSError, plistlib.InvalidFileException) as exc:
            raise SystemExit(f"无法确认现有应用归属，不会覆盖：{target}") from exc
        if existing.get("CFBundleIdentifier") != "local.tradingagents.workspace":
            raise SystemExit(f"不会覆盖不属于本项目的应用：{target}")

    with tempfile.TemporaryDirectory(prefix="tradingagents-app-") as directory:
        build = Path(directory)
        source = build / "launch.applescript"
        source.write_text(launcher_script(ROOT, sys.executable, DATA), encoding="utf-8")
        app = build / "TradingAgents.app"
        subprocess.run(["osacompile", "-o", str(app), str(source)], check=True)
        shutil.copyfile(
            Path(__file__).parent / "assets/app-icon.icns", app / "Contents/Resources/applet.icns"
        )
        info_path = app / "Contents/Info.plist"
        info = plistlib.loads(info_path.read_bytes())
        info.update(
            CFBundleName="TradingAgents 投研台",
            CFBundleDisplayName="TradingAgents 投研台",
            CFBundleIdentifier="local.tradingagents.workspace",
            CFBundleShortVersionString="2.0.0",
        )
        info_path.write_bytes(plistlib.dumps(info))
        subprocess.run(["xattr", "-cr", str(app)], check=True)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
        staged = target.with_name(".TradingAgents-workspace-install.app")
        if staged.exists():
            raise SystemExit(f"安装暂存目录已存在，请检查后再运行：{staged}")
        shutil.copytree(app, staged, copy_function=shutil.copy)
        try:
            if target.exists():
                shutil.rmtree(target)
            staged.rename(target)
        finally:
            if staged.exists():
                shutil.rmtree(staged)
    print(target)


if __name__ == "__main__":
    main()
