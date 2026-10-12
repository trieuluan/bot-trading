FROM python:3.13-slim
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
RUN useradd --create-home trader
COPY --chown=trader:trader backend /app/backend
COPY --chown=trader:trader bot /app/bot
COPY --chown=trader:trader migrations /app/migrations
COPY --chown=trader:trader alembic.ini /app/alembic.ini
USER trader
