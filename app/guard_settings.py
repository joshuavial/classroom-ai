"""Guard category settings and their admin API."""

import unicodedata
from copy import deepcopy

from psycopg.types.json import Jsonb
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import auth

SETTINGS_KEY = "guard_categories"
ACTIONS = frozenset({"allow_flag", "block_flag", "block"})
VERSION = 1
MAX_CATEGORY_LENGTH = 100
MAX_MESSAGE_LENGTH = 500


class InvalidGuardSettings(ValueError):
    pass


def normalized_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def _keys(value: dict, expected: set[str]) -> None:
    if set(value) != expected:
        raise InvalidGuardSettings("unexpected fields")


def _message(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not 1 <= len(value) <= MAX_MESSAGE_LENGTH:
        raise InvalidGuardSettings("invalid message")
    return value


def _action(value: object) -> str:
    if not isinstance(value, str) or value not in ACTIONS:
        raise InvalidGuardSettings("invalid action")
    return value


def validate_document(value: object) -> dict:
    """Return a detached, validated settings document."""
    if not isinstance(value, dict):
        raise InvalidGuardSettings("settings must be an object")
    _keys(value, {"version", "categories", "uncategorized"})
    if type(value["version"]) is not int or value["version"] != VERSION:
        raise InvalidGuardSettings("invalid version")
    categories = value["categories"]
    fallback = value["uncategorized"]
    if not isinstance(categories, dict) or not isinstance(fallback, dict):
        raise InvalidGuardSettings("invalid settings shape")
    _keys(fallback, {"action", "message"})

    cleaned: dict[str, dict] = {}
    seen: set[str] = set()
    for display, record in categories.items():
        if not isinstance(display, str) or display != display.strip() or not 1 <= len(display) <= MAX_CATEGORY_LENGTH:
            raise InvalidGuardSettings("invalid category name")
        if any(unicodedata.category(char) == "Cc" for char in display):
            raise InvalidGuardSettings("invalid category name")
        match = normalized_name(display)
        if not match or match == "uncategorized" or match in seen:
            raise InvalidGuardSettings("duplicate or reserved category")
        seen.add(match)
        if not isinstance(record, dict):
            raise InvalidGuardSettings("invalid category")
        _keys(record, {"enabled", "action", "message"})
        if type(record["enabled"]) is not bool:
            raise InvalidGuardSettings("invalid enabled value")
        cleaned[display] = {
            "enabled": record["enabled"],
            "action": _action(record["action"]),
            "message": _message(record["message"]),
        }
    return {
        "version": VERSION,
        "categories": cleaned,
        "uncategorized": {
            "action": _action(fallback["action"]),
            "message": _message(fallback["message"]),
        },
    }


async def load(conn) -> dict:
    row = await (await conn.execute("SELECT value FROM settings WHERE key = %s", (SETTINGS_KEY,))).fetchone()
    if row is None:
        raise InvalidGuardSettings("guard settings are missing")
    return validate_document(row[0])


async def save(conn, value: object) -> dict:
    """Replace the document atomically. Concurrent commits use last-writer-wins."""
    document = validate_document(value)
    await conn.execute(
        "INSERT INTO settings (key, value) VALUES (%s, %s)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        (SETTINGS_KEY, Jsonb(document)),
    )
    return deepcopy(document)


def _configuration(document: dict) -> list[dict]:
    return [
        {"name": name, "enabled": row["enabled"], "action": row["action"]}
        for name, row in document["categories"].items()
    ] + [{"name": "Uncategorized", "enabled": True, "action": document["uncategorized"]["action"]}]


async def get_settings(request: Request) -> JSONResponse:
    await auth.require_staff(request, "admin")
    async with request.app.state.pool.connection() as conn:
        try:
            document = await load(conn)
        except InvalidGuardSettings:
            raise auth.HTTPError(503, "guard_configuration_invalid") from None
    return JSONResponse(document, headers={"Cache-Control": "no-store"})


async def put_settings(request: Request) -> JSONResponse:
    admin = await auth.require_staff(request, "admin")
    body = await auth.json_body(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        try:
            current = await load(conn)
        except InvalidGuardSettings:
            raise auth.HTTPError(503, "guard_configuration_invalid") from None
        try:
            replacement = validate_document(body)
        except InvalidGuardSettings:
            raise auth.HTTPError(400, "bad_guard_settings") from None
        if set(replacement["categories"]) != set(current["categories"]):
            raise auth.HTTPError(400, "bad_guard_settings")
        saved = await save(conn, replacement)
        await auth.audit(
            conn,
            admin["id"],
            admin["username"],
            "guard.settings",
            {"before": _configuration(current), "after": _configuration(saved)},
        )
    return JSONResponse(saved, headers={"Cache-Control": "no-store"})


routes = [
    Route("/api/admin/guard", get_settings),
    Route("/api/admin/guard", put_settings, methods=["PUT"]),
]
