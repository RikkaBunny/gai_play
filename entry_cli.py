"""PyInstaller 入口 - 独立命令行工具"""
import sys
import os

# 让打包后的 exe 能找到 skills 和 config.yaml
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))

from gai_mcp.cli import main
main()
