FROM python:3.13-slim AS runtime
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
RUN useradd --create-home trader
RUN mkdir -p /tmp/futures-replay-cache && chown trader:trader /tmp/futures-replay-cache
COPY --chown=trader:trader backend /app/backend
COPY --chown=trader:trader bot /app/bot
COPY --chown=trader:trader scripts /app/scripts
COPY --chown=trader:trader migrations /app/migrations
COPY --chown=trader:trader alembic.ini /app/alembic.ini
USER trader

FROM runtime AS development
USER root
COPY backend/requirements-dev.txt /app/backend/requirements-dev.txt
RUN pip install --no-cache-dir -r backend/requirements-dev.txt
USER trader

FROM runtime AS production
