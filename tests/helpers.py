"""Shared helpers for API tests."""

import secrets

import httpx
import psycopg

from app import auth

PASSWORD = "correct horse battery"
_hash: str | None = None


async def password_hash() -> str:
    global _hash
    if _hash is None:
        _hash = await auth.hash_password(PASSWORD)
    return _hash


async def make_staff(dsn: str, role: str = "teacher") -> str:
    username = f"{role}-{secrets.token_hex(4)}"
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO staff (username, password_hash, role) VALUES (%s, %s, %s)",
            (username, await password_hash(), role),
        )
    return username


async def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    if "csrf_token" not in client.cookies:
        await client.get("/api/me")
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


async def post(client, path: str, body: dict | None = None):
    return await client.post(path, json=body or {}, headers=await csrf(client))


async def sign_in(client, username: str) -> None:
    response = await post(client, "/api/login", {"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text


def new_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
