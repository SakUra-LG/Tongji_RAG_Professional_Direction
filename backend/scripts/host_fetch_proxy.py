"""Temporary host-side HTTPS fetch proxy for Docker Desktop TLS issues."""

from __future__ import annotations

import argparse
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse


ALLOWED_HOSTS = {
    "baike.baidu.com",
    "www.tongji.edu.cn",
    "tongji.edu.cn",
    "cs.tongji.edu.cn",
}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


class FetchHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/fetch":
            self.send_error(404)
            return

        target = parse_qs(parsed.query).get("url", [""])[0]
        target_host = urlparse(target).netloc.lower()
        if target_host not in ALLOWED_HOSTS:
            self.send_error(403, "Host is not allowed")
            return

        command = [
            "curl.exe",
            "-L",
            "--http1.1",
            "-k",
            "-A",
            USER_AGENT,
            "--connect-timeout",
            "15",
            "--max-time",
            "45",
            "--retry",
            "3",
            "--retry-all-errors",
            "-sS",
            "-w",
            "\n__FINAL_URL__:%{url_effective}",
            target,
        ]
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                timeout=55,
            )
            marker = b"\n__FINAL_URL__:"
            body, separator, final_url = completed.stdout.rpartition(marker)
            if not separator:
                body = completed.stdout
                final_url = target.encode()
        except Exception as exc:  # noqa: BLE001
            self.send_error(502, str(exc))
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header(
            "X-Final-Url",
            quote(
                final_url.decode("utf-8", errors="replace"),
                safe=":/?&=%#",
            ),
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(format % args, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), FetchHandler)
    print(f"Host fetch proxy listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
