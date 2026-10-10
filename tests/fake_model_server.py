"""Serve the fake worker (tests/fakes.py) on a port, for the Playwright smoke:
an OpenAI-compatible model server that answers every chat with a canned,
streamed reply. Run: uv run python -m tests.fake_model_server --port 18092
"""

import argparse

import uvicorn

from tests.fakes import fake_worker

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18092)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(fake_worker(models=["gemma-4-e2b-it"], reply=["Cells ", "are ", "the ", "units ", "of ", "life."]),
                host=args.host, port=args.port, log_level="warning")
