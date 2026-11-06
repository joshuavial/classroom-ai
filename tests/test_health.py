import httpx

from app.main import create_app
from tests.fakes import fake_guard


async def test_healthz_ok(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "guard": "ok"}


async def test_healthz_reports_database_down(dsn):
    app = create_app(dsn, guard_transport=httpx.ASGITransport(app=fake_guard()))
    async with app.router.lifespan_context(app):
        await app.state.pool.close()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
             response = await client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "down", "db": "down", "guard": "ok"}


async def test_healthz_reports_guard_down(dsn):
    async def down(request):
        raise httpx.ConnectError("down", request=request)

    app = create_app(dsn, guard_transport=httpx.MockTransport(down))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "down", "db": "ok", "guard": "down"}
