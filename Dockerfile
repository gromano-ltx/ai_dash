FROM node:20-slim AS frontend
WORKDIR /app
COPY frontend/package*.json frontend/
RUN cd frontend && npm ci --quiet
COPY frontend/ frontend/
RUN cd frontend && npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY backend/ backend/
COPY collector/ collector/
COPY install.sh .
COPY --from=frontend /app/frontend/dist frontend/dist
ENV PORT=8080
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port $PORT"]
