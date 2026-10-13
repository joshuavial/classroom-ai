"""The session Postgres must not leave Docker volumes behind."""

import json
import subprocess

from tests.conftest import drop_container


def _inspect(name: str) -> dict:
    out = subprocess.run(["docker", "inspect", name], check=True, capture_output=True, text=True).stdout
    return json.loads(out)[0]


def _session_container(pg_server: str) -> str:
    port = pg_server.rsplit(":", 1)[1].split("/")[0]
    names = subprocess.run(
        ["docker", "ps", "--filter", "name=classroom-ai-test-", "--format", "{{.Names}}"],
        check=True, capture_output=True, text=True).stdout.split()
    return next(name for name in names if subprocess.run(
        ["docker", "port", name, "5432/tcp"], capture_output=True, text=True).stdout.strip().endswith(f":{port}"))


def test_session_postgres_keeps_data_on_tmpfs_and_teardown_drops_volumes(pg_server):
    info = _inspect(_session_container(pg_server))
    mounts = info["Mounts"]
    assert [mount["Type"] for mount in mounts if mount["Type"] != "tmpfs"] == []
    assert "/var/lib/postgresql" in (info["HostConfig"]["Tmpfs"] or {})

    # The image declares a volume. Teardown has to delete that too, or a run
    # without the tmpfs mount leaves it behind after the container is gone.
    leftover = f"classroom-ai-test-teardown-{pg_server.rsplit(':', 1)[1].split('/')[0]}"
    volumes: list[str] = []
    subprocess.run(["docker", "create", "--name", leftover, info["Config"]["Image"]],
                   check=True, capture_output=True)
    try:
        volumes = [mount["Name"] for mount in _inspect(leftover)["Mounts"] if mount["Type"] == "volume"]
        assert volumes
        drop_container(leftover)
        assert subprocess.run(["docker", "inspect", leftover], capture_output=True).returncode != 0
        for volume in volumes:
            assert subprocess.run(["docker", "volume", "inspect", volume], capture_output=True).returncode != 0
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", leftover], capture_output=True)
        for volume in volumes:
            subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True)
