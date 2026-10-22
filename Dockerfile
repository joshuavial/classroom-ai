# The app image: the Python API and gateway.
FROM ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b AS uv

FROM python:3.14.8-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/venv UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
# pg_dump for the admin page's backup download. Its version moves with the
# postgres image in compose.yml.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSo /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-18=18.6-1.pgdg13+2 \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
RUN useradd --system --no-create-home app
USER app
EXPOSE 8000
# Open live streams would otherwise hold a stop until Docker kills the process.
CMD ["/venv/bin/uvicorn", "app.main:from_env", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "5"]
