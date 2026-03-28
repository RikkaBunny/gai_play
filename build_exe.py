"""PyInstaller 打包入口 - 生成 gai-play.exe (Web 控制台)"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "src" / "gai_play"
SKILLS = ROOT / "skills"
STATIC = SRC / "web" / "static"

COMMON_ARGS = [
    f"--paths={ROOT / 'src'}",
    f"--add-data={SKILLS};skills",
    "--hidden-import=win32gui",
    "--hidden-import=win32ui",
    "--hidden-import=win32con",
    "--hidden-import=win32api",
    "--hidden-import=pywintypes",
    "--hidden-import=anthropic",
    "--hidden-import=openai",
    "--hidden-import=httpx",
    "--hidden-import=yaml",
    "--hidden-import=pyautogui",
    "--hidden-import=pyperclip",
    "--hidden-import=pyvda",
    "--hidden-import=mss",
    "--hidden-import=numpy",
    "--hidden-import=PIL",
    "--hidden-import=pydantic",
    "--noconfirm",
    "--clean",
]


def build():
    """打包 gai-play.exe (Web 控制台)"""
    print("=" * 50)
    print("打包 gai-play.exe ...")
    print("=" * 50)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=gai-play",
        "--console",
        f"--add-data={STATIC};gai_play/web/static",
        "--hidden-import=uvicorn",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.http.h11_impl",
        "--hidden-import=uvicorn.protocols.websockets",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.lifespan",
        "--hidden-import=uvicorn.lifespan.on",
        "--hidden-import=starlette",
        "--hidden-import=starlette.routing",
        "--hidden-import=starlette.responses",
        "--hidden-import=starlette.staticfiles",
        *COMMON_ARGS,
        str(ROOT / "entry_web.py"),
    ]
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    build()
    print("\n" + "=" * 50)
    print("打包完成！输出目录: dist/gai-play/")
    print("  dist/gai-play/gai-play.exe")
    print("=" * 50)
