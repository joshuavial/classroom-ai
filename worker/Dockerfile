# The worker agent image: auth proxy and heartbeat in front of a model server.
FROM python:3.14.8-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir --no-deps -r requirements.txt && pip check
COPY agent.py ./
RUN useradd --system --no-create-home agent && mkdir /data && chown agent /data
USER agent
# Holds the worker ID and API key across restarts.
VOLUME /data
EXPOSE 8081
CMD ["uvicorn", "agent:from_env", "--factory", "--host", "0.0.0.0", "--port", "8081"]
