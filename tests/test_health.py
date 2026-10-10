from app.main import create_app


async def test_healthz_ok(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


async def test_healthz_reports_database_down(dsn):
    import httpx

    app = create_app(dsn)
    async with app.router.lifespan_context(app):
        await app.state.pool.close()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "down", "db": "down"}
