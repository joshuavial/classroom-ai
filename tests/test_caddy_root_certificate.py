"""Named, rerunnable Caddy integration coverage for the root certificate.

Run with: uv run pytest tests/test_caddy_root_certificate.py
It uses the exact pinned Caddy image from compose.yml and a local fake app,
not a globally installed Caddy or a real CA volume.
"""

import http.client
import secrets
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


CADDY_IMAGE = "caddy:2.11.7@sha256:f2a1290d0463aad60660d4ec134943f183ee2a5f6c3eb7bf32dd984f2f020772"
ROOT_PATH = Path("caddy/pki/authorities/local/root.crt")


class FakeApp(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/internal/root-certificate-authorize":
            role = self.headers.get("Cookie", "")
            status = 204 if "role=admin" in role else 403 if "role=teacher" in role else 401
            self.send_response(status)
            self.end_headers()
            return
        self.send_response(418)
        self.end_headers()
        self.wfile.write(b"unrelated upstream")

    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def fake_app():
    # Bind 0.0.0.0 on an ephemeral port so the Caddy container can reach the host.
    server = ThreadingHTTPServer(("0.0.0.0", 0), FakeApp)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        thread.join()


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


class Caddy:
    def __init__(self, fake_port: int, missing: bool = False, app_port: int | None = None):
        self.name = f"classroom-ai-caddy-root-{secrets.token_hex(4)}"
        self.volume = f"{self.name}-data"
        self.fake_port = fake_port
        self.app_port = app_port or fake_port
        self.missing = missing

    def __enter__(self):
        repo = Path(__file__).resolve().parents[1]
        command = [
            "run", "-d", "--name", self.name,
            "--add-host", "host.docker.internal:host-gateway",
            "-e", "SITE_ADDRESS=:80",
            "-e", "SERVER_NAME=localhost",
            "-e", f"APP_UPSTREAM=host.docker.internal:{self.app_port}",
            "-e", f"WEB_UPSTREAM=host.docker.internal:{self.fake_port}",
            "-p", "127.0.0.1::80",
            "-v", f"{repo / 'Caddyfile'}:/etc/caddy/Caddyfile:ro",
            "-v", f"{self.volume}:/data",
        ]
        if self.missing:
            command.extend(["-e", "ROOT_CERTIFICATE_DIR=/missing"])
        try:
            docker(*command, CADDY_IMAGE)
        except subprocess.CalledProcessError:
            self.cleanup()
            raise
        try:
            binding = docker("port", self.name, "80/tcp").splitlines()[0]
        except (subprocess.CalledProcessError, IndexError):
            logs = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True).stderr
            self.cleanup()
            raise RuntimeError(f"Caddy exited during startup:\n{logs}") from None
        self.port = int(binding.rsplit(":", 1)[1])
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                status, _, _ = self.request("/unrelated")
                if status == 418:
                    return self
            except OSError:
                pass
            time.sleep(0.1)
        logs = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True).stderr
        self.cleanup()
        raise RuntimeError(f"Caddy did not start:\n{logs}")

    def __exit__(self, *unused):
        self.cleanup()

    def cleanup(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", "-f", self.volume], capture_output=True)

    def request(
        self,
        path: str,
        method: str = "GET",
        cookie: str | None = None,
        headers: dict[str, str] | None = None,
    ):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        request_headers = dict(headers or {})
        if cookie:
            request_headers["Cookie"] = cookie
        connection.request(method, path, headers=request_headers)
        response = connection.getresponse()
        status, headers, body = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return status, headers, body

    def root_bytes(self) -> bytes:
        return subprocess.run(
            ["docker", "exec", self.name, "cat", f"/data/{ROOT_PATH}"],
            check=True,
            capture_output=True,
        ).stdout

    def ca_state(self) -> bytes:
        return subprocess.run(
            [
                "docker", "exec", self.name, "sh", "-c",
                "stat -c '%a:%u:%g:%n' /data/caddy/pki/authorities/local/root.crt "
                "/data/caddy/pki/authorities/local/root.key && "
                "sha256sum /data/caddy/pki/authorities/local/root.crt "
                "/data/caddy/pki/authorities/local/root.key",
            ],
            check=True,
            capture_output=True,
        ).stdout


@pytest.fixture(scope="module", autouse=True)
def docker_available():
    if not shutil.which("docker"):
        pytest.skip("Docker is required for the Caddy integration test")


def test_caddy_root_certificate_route(fake_app):
    with Caddy(fake_app) as caddy:
        root_bytes = caddy.root_bytes()
        ca_state = caddy.ca_state()
        status, headers, body = caddy.request("/api/admin/root-certificate", cookie="role=admin")
        assert (status, body) == (200, root_bytes)
        assert headers["Cache-Control"] == "no-store"
        assert headers["Content-Type"] == "application/x-x509-ca-cert"
        assert headers["Content-Disposition"] == 'attachment; filename="classroom-ai-root.crt"'

        for request_headers in (
            {"Range": "bytes=999999999-"},
            {"Range": "bytes=0-9"},
            {"If-None-Match": headers["Etag"]},
            {"If-Match": '"not-the-certificate"'},
            {"If-Modified-Since": headers["Last-Modified"]},
            {"If-Unmodified-Since": "Thu, 01 Jan 1970 00:00:00 GMT"},
        ):
            conditional_status, conditional_headers, conditional_body = caddy.request(
                "/api/admin/root-certificate",
                cookie="role=admin",
                headers=request_headers,
            )
            assert (conditional_status, conditional_body) == (200, root_bytes)
            assert conditional_headers["Cache-Control"] == "no-store"
            assert conditional_headers["Content-Type"] == "application/x-x509-ca-cert"
            assert conditional_headers["Content-Disposition"] == (
                'attachment; filename="classroom-ai-root.crt"'
            )

        for cookie, expected in ((None, 401), ("role=teacher", 403)):
            status, headers, body = caddy.request("/api/admin/root-certificate", cookie=cookie)
            assert status == expected
            assert headers["Cache-Control"] == "no-store"
            assert headers.get("Content-Type") != "application/x-x509-ca-cert"
            assert "Content-Disposition" not in headers
            assert root_bytes not in body

        for method in ("POST", "HEAD"):
            status, headers, body = caddy.request("/api/admin/root-certificate", method, "role=admin")
            assert status == 405
            assert headers["Cache-Control"] == "no-store"
            assert "Content-Disposition" not in headers
            assert root_bytes not in body

        for cookie in (None, "role=admin"):
            for path in (
                "/api/admin/root-certificate/",
                "/API/admin/root-certificate",
                "/api%2fadmin/root-certificate",
                "/api/admin/%72oot-certificate",
                "/api/admin/root%2dcertificate",
                "/api/admin/%2e%2e/root-certificate",
                "/api/admin/root-certificate?download=1",
                "/api/admin/root.key",
            ):
                status, _, body = caddy.request(path, cookie=cookie)
                assert not 200 <= status < 300, (cookie, path)
                assert root_bytes not in body

        status, _, body = caddy.request("/unrelated")
        assert (status, body) == (418, b"unrelated upstream")
        assert caddy.request("/internal/root-certificate-authorize")[0] == 404
        assert caddy.root_bytes() == root_bytes
        assert caddy.ca_state() == ca_state


def test_caddy_root_certificate_missing_file_is_route_local(fake_app):
    with Caddy(fake_app, missing=True) as caddy:
        root_bytes = caddy.root_bytes()
        status, headers, body = caddy.request("/api/admin/root-certificate", cookie="role=admin")
        assert not 200 <= status < 300
        assert headers["Cache-Control"] == "no-store"
        assert "Content-Disposition" not in headers
        assert root_bytes not in body
        status, _, body = caddy.request("/unrelated")
        assert (status, body) == (418, b"unrelated upstream")


def test_caddy_root_certificate_fails_closed_when_auth_is_unavailable(fake_app):
    # Bind 0.0.0.0 on an ephemeral port so the Caddy container can reach the host.
    unavailable = ThreadingHTTPServer(("0.0.0.0", 0), FakeApp)
    thread = threading.Thread(target=unavailable.serve_forever, daemon=True)
    thread.start()
    try:
        with Caddy(fake_app, app_port=unavailable.server_port) as caddy:
            unavailable.shutdown()
            unavailable.server_close()
            thread.join()
            root_bytes = caddy.root_bytes()
            status, headers, body = caddy.request(
                "/api/admin/root-certificate",
                cookie="role=admin",
            )
            assert not 200 <= status < 300
            assert headers["Cache-Control"] == "no-store"
            assert headers.get("Content-Type") != "application/x-x509-ca-cert"
            assert "Content-Disposition" not in headers
            assert root_bytes not in body
            status, _, body = caddy.request("/unrelated")
            assert (status, body) == (418, b"unrelated upstream")
    finally:
        unavailable.shutdown()
        unavailable.server_close()
        thread.join()
