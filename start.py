from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
# 同时兼容项目自己的环境变量名和较短的旧变量名。
PROVIDER_KEYS = {
    "ALPHA_MARKET_DATA_PROVIDER",
    "MARKET_DATA_PROVIDER",
}


def configure_stream_encoding(stream: object) -> None:
    """尽量把控制台输出切到 UTF-8，避免 Windows 下中文乱码。"""

    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")


configure_stream_encoding(sys.stdout)
configure_stream_encoding(sys.stderr)


def parse_args() -> argparse.Namespace:
    """解析本地启动参数。"""

    parser = argparse.ArgumentParser(description="启动 AlphaEngine 前后端一体服务。")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址，默认 127.0.0.1。")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="起始端口，默认 8000。")
    parser.add_argument(
        "--provider",
        choices=["hybrid", "finnhub", "polygon", "alphavantage", "eastmoney"],
        help="覆盖行情源；不指定时优先使用系统环境变量或 .env。",
    )
    parser.add_argument("--reload", action="store_true", help="开启 uvicorn 热重载。")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器。")
    return parser.parse_args()


def env_file_defines_market_provider(env_path: Path) -> bool:
    """检查 .env 是否已经显式声明行情源。"""

    if not env_path.exists():
        return False
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        # 跳过空行、注释和非法行，避免把注释里的变量名误判为配置。
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key in PROVIDER_KEYS:
            return True
    return False


def apply_market_provider(args: argparse.Namespace, env: dict[str, str]) -> str:
    """确定本次启动使用的行情源，并在需要时写入子进程环境变量。

    优先级：命令行 --provider > 当前系统环境变量 > .env > 默认 hybrid。
    """

    if args.provider:
        env["ALPHA_MARKET_DATA_PROVIDER"] = args.provider
        return args.provider
    if any(env.get(key) for key in PROVIDER_KEYS):
        return env.get("ALPHA_MARKET_DATA_PROVIDER") or env.get("MARKET_DATA_PROVIDER") or "hybrid"
    if env_file_defines_market_provider(ROOT_DIR / ".env"):
        # .env 会由 pydantic-settings 在子进程里读取，这里只返回展示文本。
        return "来自 .env"
    env["ALPHA_MARKET_DATA_PROVIDER"] = "hybrid"
    return "hybrid"


def port_is_available(host: str, port: int) -> bool:
    """通过尝试 bind 判断端口是否可用。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def find_available_port(host: str, start_port: int) -> int:
    """从起始端口开始向后寻找可用端口。"""

    for port in range(start_port, start_port + 50):
        if port_is_available(host, port):
            return port
    raise RuntimeError(f"从 {start_port} 开始的 50 个端口都不可用。")


def build_command(host: str, port: int, reload: bool) -> list[str]:
    """构建 uvicorn 启动命令。

    优先使用 uv run，能自动进入项目依赖环境；没有 uv 时退回当前 Python。
    """

    uv_path = shutil.which("uv")
    if uv_path:
        command = [uv_path, "run", "uvicorn", "app.main:app"]
    else:
        if importlib.util.find_spec("uvicorn") is None:
            raise RuntimeError("未找到 uv，也无法通过当前 Python 导入 uvicorn。")
        command = [sys.executable, "-m", "uvicorn", "app.main:app"]

    command.extend(["--host", host, "--port", str(port)])
    if reload:
        command.append("--reload")
    return command


def wait_until_ready(url: str, timeout_seconds: float = 20) -> bool:
    """轮询根路径，确认服务已经能接受请求。"""

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return 200 <= response.status < 500
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    return False


def main() -> int:
    """本地开发的一键启动入口。"""

    args = parse_args()
    env = os.environ.copy()
    provider = apply_market_provider(args, env)
    port = find_available_port(args.host, args.port)
    url = f"http://{args.host}:{port}/"
    command = build_command(args.host, port, args.reload)

    print("AlphaEngine 正在启动...")
    print(f"工作目录：{ROOT_DIR}")
    print(f"行情源：{provider}")
    print(f"访问地址：{url}")
    print("停止服务：在此窗口按 Ctrl+C")

    process = subprocess.Popen(command, cwd=ROOT_DIR, env=env)
    try:
        # 服务可访问后再打开浏览器，避免浏览器先出现连接失败页面。
        if wait_until_ready(url) and not args.no_browser:
            webbrowser.open(url)
        return process.wait()
    except KeyboardInterrupt:
        # Ctrl+C 时优雅终止 uvicorn；若 8 秒内未退出，再强制结束。
        print("\n正在停止 AlphaEngine...")
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
