"""Exercise the smoke launcher without starting its Docker stack."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import signal


ROOT = Path(__file__).resolve().parents[1]


def test_smoke_backend_listens_on_all_host_interfaces(tmp_path):
    with socket.socket() as port_socket:
        port_socket.bind(("0.0.0.0", 0))
        port = port_socket.getsockname()[1]

    repo = tmp_path / "repo"
    e2e = repo / "web" / "e2e"
    e2e.mkdir(parents=True)
    launcher = e2e / "run.sh"
    launcher.write_bytes((ROOT / "web/e2e/run.sh").read_bytes())
    (repo / ".env").touch()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    # Observe the real socket after a successful bind, then run the unmodified
    # fake model module. No local network permission or physical device needed.
    uv.write_text(f"#!{sys.executable}\n" + '''import os, runpy, socket, sys
from pathlib import Path
original = socket.socket.bind
def bind(sock, address):
    result = original(sock, address)
    if isinstance(address, tuple) and address[1] == int(os.environ["FAKE_MODEL_PORT"]):
        Path(os.environ["SMOKE_BIND_RESULT"]).write_text(sock.getsockname()[0])
    return result
socket.socket.bind = bind
args = sys.argv[3:]
assert args[0] == "-m"
sys.argv = args[1:]
runpy.run_module(args[1], run_name="__main__")
''')
    uv.chmod(0o755)
    # Probe the real server after the launcher starts it, then stop before the
    # expensive stack/browser setup. This still exercises the launcher's trap.
    docker = binaries / "docker"
    docker.write_text(f"#!{sys.executable}\n" + '''import http.client, json, os, sys, time
from pathlib import Path
if "up" not in sys.argv:
    raise SystemExit(0)
deadline = time.monotonic() + 3
while time.monotonic() < deadline:
    connection = http.client.HTTPConnection("127.0.0.1", int(os.environ["FAKE_MODEL_PORT"]), timeout=0.2)
    try:
        connection.request("GET", "/v1/models")
        response = connection.getresponse()
        body = json.loads(response.read())
        if response.status == 200 and body["data"][0]["id"] == "gemma-4-e2b-it":
            Path(os.environ["SMOKE_PROBE_RESULT"]).write_text("reachable")
            break
    except (OSError, ValueError, KeyError):
        time.sleep(0.05)
    finally:
        connection.close()
raise SystemExit(74)
''')
    docker.chmod(0o755)
    marker = tmp_path / "probe.txt"
    binding = tmp_path / "bind.txt"
    env = {**os.environ, "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
           "PYTHONPATH": str(ROOT), "FAKE_MODEL_PORT": str(port),
           "SMOKE_BIND_RESULT": str(binding), "SMOKE_PROBE_RESULT": str(marker)}
    process = subprocess.Popen(["/bin/sh", str(launcher)], env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        _, stderr = process.communicate(timeout=15)
        assert process.returncode == 74, stderr
        assert marker.exists(), f"fake model did not become ready: {stderr}"
        assert marker.read_text() == "reachable"
        assert binding.read_text() == "0.0.0.0", "smoke model must accept container connections on every host interface"
        # The launcher, rather than this test's fallback cleanup, stopped it.
        with socket.socket() as probe:
            probe.settimeout(0.5)
            assert probe.connect_ex(("127.0.0.1", port)) != 0
    finally:
        # Also clean up descendants if a regression breaks the shell trap.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
