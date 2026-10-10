import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_agent_pins_match_the_lockfile():
    locked = {p["name"]: p["version"] for p in tomllib.loads((ROOT / "uv.lock").read_text())["package"]}
    lines = (ROOT / "worker" / "requirements.txt").read_text().splitlines()
    pins = dict(line.split("==") for line in lines if line and not line.startswith("#"))
    assert {"starlette", "uvicorn", "httpx"} <= set(pins)
    assert {name: locked.get(name) for name in pins} == pins
