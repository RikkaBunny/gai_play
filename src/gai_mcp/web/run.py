"""启动 Web 配置后台"""

from __future__ import annotations

import webbrowser
import threading

import uvicorn

from ..config_manager import setup_file_logging
from .app import create_app


def open_browser(port: int) -> None:
    """延迟打开浏览器"""
    import time
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{port}")


def main(port: int = 9966, open_ui: bool = True) -> None:
    """启动配置后台

    Args:
        port: 端口号
        open_ui: 是否自动打开浏览器
    """
    setup_file_logging()

    app = create_app()

    if open_ui:
        threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    print(f"\n  GAI MCP 配置面板已启动: http://localhost:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
