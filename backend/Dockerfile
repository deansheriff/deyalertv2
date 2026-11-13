FROM python:3.12-slim AS base
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./

FROM base AS dev
RUN uv sync --frozen
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS runtime
RUN uv sync --frozen --no-dev
COPY . .
RUN useradd --create-home deyalert
USER deyalert
# Access logs are off because tracking and consent links carry tokens in the path.
# Only Coolify's proxy can reach this port, so its forwarded client address is trusted.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
