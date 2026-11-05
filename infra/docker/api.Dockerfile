FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/apps/api
COPY apps/api/ ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY data/ /workspace/data/
RUN groupadd --gid 10001 heatshift && useradd --uid 10001 --gid heatshift --no-create-home heatshift \
    && chown -R heatshift:heatshift /workspace
USER heatshift

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
