"""PyInstaller 入口：启动本地 UI 服务。
打包：python -m PyInstaller --onefile --name endfield-server packaging/entry.py
"""
import sys

from endfield.web import server

if __name__ == "__main__":
    port = 8777
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    server.serve(port=port, open_browser=True)
