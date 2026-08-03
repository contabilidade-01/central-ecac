import threading
import time
import webbrowser
import socket


def _is_port_open(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def open_browser_when_ready(url: str):
    def _worker():
        for _ in range(60):
            if _is_port_open('127.0.0.1', 5847):
                webbrowser.open(url)
                return
            time.sleep(0.5)
        webbrowser.open(url)

    threading.Thread(target=_worker, daemon=True).start()
