"""Test doubles for the GPU worker and the guard, as small Starlette apps.

Drive them in-process with httpx.ASGITransport, so tests need no models.
"""

import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route


def fake_worker(
    models: list[str] | None = None,
    reply: list[str] | None = None,
    api_key: str | None = None,
) -> Starlette:
    """An OpenAI-compatible model server that streams a canned reply."""
    models = models or ["gemma-4-e2b-it"]
    reply = reply or ["Hello", " there", "."]

    def authorised(request: Request) -> bool:
        return api_key is None or request.headers.get("authorization") == f"Bearer {api_key}"

    async def list_models(request: Request):
        if not authorised(request):
            return JSONResponse({"error": "unauthorised"}, status_code=401)
        return JSONResponse({"object": "list", "data": [{"id": m, "object": "model"} for m in models]})

    async def chat(request: Request):
        if not authorised(request):
            return JSONResponse({"error": "unauthorised"}, status_code=401)
        body = await request.json()

        async def chunks():
            for piece in reply:
                event = {"object": "chat.completion.chunk", "model": body.get("model"),
                         "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                yield f"data: {json.dumps(event)}\n\n"
            done = {"object": "chat.completion.chunk", "model": body.get("model"),
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(done)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    return Starlette(routes=[
        Route("/v1/models", list_models),
        Route("/v1/chat/completions", chat, methods=["POST"]),
    ])


def fake_guard(verdict: str = "Safety: Safe\nCategories: None") -> Starlette:
    """A llama-server stand-in that answers every check with a Qwen3Guard verdict."""

    async def chat(request: Request):
        await request.json()
        return JSONResponse({
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": verdict},
                         "finish_reason": "stop"}],
        })

    return Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"])])
