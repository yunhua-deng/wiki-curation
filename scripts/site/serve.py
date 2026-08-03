#!/usr/bin/env python3
"""
scripts/site/serve.py — 本地 HTTP 服务包装器。

以 wiki 工作区为根目录启动 http.server，使 /site/ 与 /artifacts/ 均可访问。
"""
import argparse
import atexit
import json
import os
import platform
import signal
import socket
import subprocess
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from scripts import paths


class SPAHandler(SimpleHTTPRequestHandler):
    """处理目录根路径自动补全 index.html，并为文本响应添加 UTF-8 charset。"""

    def do_GET(self):
        # v3.5：survey 状态 API
        if self.path.startswith("/api/survey/status"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            from scripts.site import api as site_api
            code, data = site_api.handle_survey_status(self.directory, (q.get("id") or [""])[0])
            self._send_json(code, data)
            return
        # v3.3：根路径与 /index.html 直接跳到站点首页（用户只记端口即可）
        if self.path in ("/", "/index.html"):
            self.send_response(301)
            self.send_header("Location", "/site/")
            self.end_headers()
            return
        super().do_GET()

    def _read_json_body(self):
        """读取并解析 POST body。返回 (payload, error_response_or_None)。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}, None
        try:
            return json.loads(raw), None
        except Exception:
            return None, (400, {"ok": False, "error": "INVALID_JSON",
                                "message": "request body is not valid JSON (expect UTF-8)"})

    def do_POST(self):
        # v3.5：survey 发起 API（仅 loopback；api 层再校验）
        if self.path.split("?")[0] == "/api/watch":
            payload, err = self._read_json_body()
            if err:
                self._send_json(*err)
                return
            from scripts.site import api as site_api
            code, data = site_api.handle_watch(
                self.directory, payload, client_ip=self.client_address[0])
            self._send_json(code, data)
            return
        if self.path.split("?")[0] == "/api/track":
            payload, err = self._read_json_body()
            if err:
                self._send_json(*err)
                return
            from scripts.site import api as site_api
            code, data = site_api.handle_track(
                self.directory, payload, client_ip=self.client_address[0])
            self._send_json(code, data)
            return
        if self.path.split("?")[0] == "/api/post":
            payload, err = self._read_json_body()
            if err:
                self._send_json(*err)
                return
            from scripts.site import api as site_api
            code, data = site_api.handle_post(
                self.directory, payload, client_ip=self.client_address[0])
            self._send_json(code, data)
            return
        if self.path.split("?")[0] == "/api/record-links":
            payload, err = self._read_json_body()
            if err:
                self._send_json(*err)
                return
            from scripts.site import api as site_api
            code, data = site_api.handle_add_link(
                self.directory, payload, client_ip=self.client_address[0])
            self._send_json(code, data)
            return
        if self.path.split("?")[0] == "/api/survey":
            payload, err = self._read_json_body()
            if err:
                self._send_json(*err)
                return
            from scripts.site import api as site_api
            code, data = site_api.handle_survey_request(
                self.directory, payload, client_ip=self.client_address[0])
            self._send_json(code, data)
            return
        self.send_error(404)

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_header(self, keyword, value):
        if keyword.lower() == "content-type" and value.startswith("text/") and "charset" not in value:
            value += "; charset=utf-8"
        super().send_header(keyword, value)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        # 抑制默认日志噪音
        pass


def _port_in_use(port: int) -> bool:
    """检查端口是否已被占用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def _write_pid_file(pid_file: Path, pid: int) -> None:
    pid_file.write_text(str(pid), encoding="utf-8")


def _remove_pid_file(pid_file: Path) -> None:
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass


def stop_server(pid_file: str | Path) -> None:
    """读取 PID 文件并终止对应进程。"""
    pid_file = Path(pid_file).resolve()
    if not pid_file.exists():
        raise FileNotFoundError(f"PID file not found: {pid_file}")

    pid = int(pid_file.read_text(encoding="utf-8").strip())
    if pid <= 0:
        raise ValueError(f"invalid PID in {pid_file}: {pid}")

    if platform.system() == "Windows":
        # taskkill without /F requests graceful termination; fall back to /F.
        result = subprocess.run(
            ["taskkill", "/PID", str(pid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
    else:
        os.kill(pid, signal.SIGTERM)

    _remove_pid_file(pid_file)


def serve(wiki_dir, port=8123, open_browser=False, quiet=False, pid_file: str | Path | None = None):
    """在 wiki_dir 根目录启动 http.server。"""
    wiki_dir = Path(wiki_dir).resolve()
    if not wiki_dir.exists():
        raise FileNotFoundError(f"wiki directory not found: {wiki_dir}")

    if _port_in_use(port):
        raise RuntimeError(
            f"port {port} is already in use. "
            "Another `site --serve` may be running. "
            "Stop it first, or use a different port with `--port`."
        )

    pid_path = Path(pid_file).resolve() if pid_file else None
    if pid_path:
        _write_pid_file(pid_path, os.getpid())
        atexit.register(_remove_pid_file, pid_path)

    import functools
    handler = functools.partial(SPAHandler, directory=str(wiki_dir))
    server = HTTPServer(("", port), handler)
    url = f"http://localhost:{port}/site/"

    if not quiet:
        print(f"Serving wiki at {url}")
        print(f"Root: {wiki_dir}")
        if pid_path:
            print(f"PID file: {pid_path}")
        print("Press Ctrl+C to stop")

    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if not quiet:
            print("\nServer stopped")
        server.shutdown()


def main():
    parser = argparse.ArgumentParser(description="本地启动 wiki 站点")
    parser.add_argument("--workspace", help="wiki 工作区路径")
    parser.add_argument("--port", type=int, default=8123, help="监听端口")
    parser.add_argument("--open", action="store_true", help="自动打开浏览器")
    parser.add_argument("--quiet", action="store_true", help="抑制输出")
    parser.add_argument("--pid-file", help="启动时将 PID 写入此文件；停止后可据此文件终止服务")
    parser.add_argument("--stop", action="store_true", help="读取 --pid-file 并终止对应进程")
    args = parser.parse_args()

    ws = Path(args.workspace) if args.workspace else paths.get_workspace()

    if args.stop:
        if not args.pid_file:
            parser.error("--stop requires --pid-file")
        stop_server(args.pid_file)
        if not args.quiet:
            print(f"Stopped server via PID file: {args.pid_file}")
        return

    serve(ws, port=args.port, open_browser=args.open, quiet=args.quiet, pid_file=args.pid_file)


if __name__ == "__main__":
    main()
