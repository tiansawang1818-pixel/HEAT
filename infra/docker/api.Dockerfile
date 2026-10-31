FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gcc g++ libpq-dev libgdal-dev gdal-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/apps/api
COPY apps/api/ ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY data/ /workspace/data/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
