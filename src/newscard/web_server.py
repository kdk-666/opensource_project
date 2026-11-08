"""Serve the local briefing UI and only the two rendered videos (not article data)."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEB_DIR = PROJECT_ROOT / "web"
VIDEO_NAMES = {"light": "newscard_light.mp4", "dark": "newscard_dark.mp4"}
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/style.css": "style.css",
    "/script.js": "script.js",
    "/assets/NotoSansKR.ttf": "assets/NotoSansKR.ttf",
    "/assets/minimal_white_sample.png": "assets/minimal_white_sample.png",
    "/assets/dark_navy_sample.png": "assets/dark_navy_sample.png",
}


class BriefingServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, data_dir: Path, web_dir: Path = DEFAULT_WEB_DIR, demo: bool = False):
        super().__init__(address, BriefingHandler)
        self.video_dir = (Path(data_dir) / "video").resolve()
        self.web_dir = Path(web_dir).resolve()
        self.demo = demo

    def video_path(self, theme: str) -> Path | None:
        name = VIDEO_NAMES.get(theme)
        if not name:
            return None
        path = (self.video_dir / name).resolve()
        # Do not follow an output symlink outside the approved video directory.
        return path if path.is_relative_to(self.video_dir) else None

    def video_manifest(self) -> dict:
        videos = {}
        for theme in VIDEO_NAMES:
            path = self.video_path(theme)
            try:
                if path is None or not path.is_file():
                    raise OSError("not found")
                info = path.stat()
                if info.st_size <= 0:
                    raise OSError("empty")
            except OSError:
                videos[theme] = {"available": False, "url": None}
            else:
                # Changing the query string prevents a stale browser video cache after regeneration.
                version = f"{info.st_mtime_ns:x}-{info.st_size:x}"
                videos[theme] = {"available": True, "url": f"/videos/{theme}?v={version}"}
        return {"videos": videos, "demo": self.demo}


def parse_range(value: str, size: int) -> tuple[int, int] | None:
    """Parse a single HTTP byte range; None means malformed or unsatisfiable."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or not size:
        return None
    first, last = match.groups()
    if (not first and not last) or len(first) > 20 or len(last) > 20:
        return None
    if not first:
        suffix = int(last)
        if suffix <= 0:
            return None
        return max(size - suffix, 0), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        return None
    return start, end


class BriefingHandler(BaseHTTPRequestHandler):
    server: BriefingServer

    def do_GET(self):
        self._handle(False)

    def do_HEAD(self):
        self._handle(True)

    def _headers(self, status: int, content_type: str, length: int):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _handle(self, head: bool):
        path = urlsplit(self.path).path
        if path == "/api/videos":
            body = json.dumps(self.server.video_manifest(), ensure_ascii=False).encode("utf-8")
            self._headers(200, "application/json; charset=utf-8", len(body))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if path in ("/videos/light", "/videos/dark"):
            self._video(path.rsplit("/", 1)[-1], head)
            return
        relative = STATIC_FILES.get(path)
        if relative is None:
            self.send_error(404)
            return
        file = (self.server.web_dir / relative).resolve()
        if not file.is_relative_to(self.server.web_dir) or not file.is_file():
            self.send_error(404)
            return
        try:
            body = file.read_bytes()
        except OSError:
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        if file.suffix in (".html", ".css", ".js"):
            content_type += "; charset=utf-8"
        self._headers(200, content_type, len(body))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _video(self, theme: str, head: bool):
        path = self.server.video_path(theme)
        try:
            if path is None:
                raise OSError("invalid video")
            stream = path.open("rb")
        except OSError:
            self.send_error(404, "Video not available")
            return
        with stream:
            size = os.fstat(stream.fileno()).st_size
            if size <= 0:
                self.send_error(404, "Video not available")
                return
            value = self.headers.get("Range")
            byte_range = parse_range(value, size) if value is not None else (0, size - 1)
            if byte_range is None:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = byte_range
            length = end - start + 1
            self._headers(206 if value is not None else 200, "video/mp4", length)
            self.send_header("Accept-Ranges", "bytes")
            if value is not None:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if not head:
                stream.seek(start)
                remaining = length
                try:
                    while remaining:
                        chunk = stream.read(min(256 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The browser can cancel an old range when seeking or changing themes.


def main():
    parser = argparse.ArgumentParser(description="로컬 브리핑 웹페이지와 완성 영상을 보여줍니다.")
    parser.add_argument("--host", default="127.0.0.1", help="기본: 로컬 PC에서만 접속")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--web-dir", default=str(DEFAULT_WEB_DIR))
    parser.add_argument("--demo", action="store_true", help="개발용 테스트 영상임을 표시")
    args = parser.parse_args()
    server = BriefingServer((args.host, args.port), Path(args.data_dir), Path(args.web_dir), args.demo)
    print(f"뉴스 브리핑 서버 시작: {args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
