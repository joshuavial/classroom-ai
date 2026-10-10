# The app image: the Python API and gateway.
FROM ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 AS uv

FROM python:3.14.8-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/venv UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
RUN useradd --system --no-create-home app
USER app
EXPOSE 8000
CMD ["/venv/bin/uvicorn", "app.main:from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
