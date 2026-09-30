"""Runs INSIDE a network-enabled sandbox (ROADMAP B4); standard library only.

The sandbox has its own empty network namespace (loopback only). This shim
listens on 127.0.0.1, forwards every connection to the host's egress proxy
through the Unix socket bound into the sandbox, points HTTP(S)_PROXY at itself,
and runs the real command. It adds no permission: the proxy enforces the
allowlist, and code that bypasses the proxy has no route anywhere.

Usage: python sandbox_egress_shim.py <unix-socket> -- <command> [args...]
"""

import os
import socket
import subprocess
import sys
import threading


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _bridge(client: socket.socket, unix_path: str) -> None:
    upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        upstream.connect(unix_path)
        back = threading.Thread(target=_pump, args=(upstream, client), daemon=True)
        back.start()
        _pump(client, upstream)
        back.join()
    except OSError:
        pass
    finally:
        upstream.close()
        client.close()


def _serve(listener: socket.socket, unix_path: str) -> None:
    while True:
        try:
            client, _ = listener.accept()
        except OSError:
            return
        threading.Thread(target=_bridge, args=(client, unix_path), daemon=True).start()


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        print("usage: sandbox_egress_shim.py <unix-socket> -- <command> ...", file=sys.stderr)
        return 2
    unix_path, command = argv[0], argv[2:]
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    threading.Thread(target=_serve, args=(listener, unix_path), daemon=True).start()

    proxy = f"http://127.0.0.1:{listener.getsockname()[1]}"
    env = dict(os.environ)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env[name] = proxy
    for name in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(name, None)
    return subprocess.run(command, env=env).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
