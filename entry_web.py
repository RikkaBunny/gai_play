"""PyInstaller 入口 - Web 配置后台"""
import sys
import os

if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))

from gai_mcp.web.run import main
main()
