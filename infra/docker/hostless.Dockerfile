FROM node:22-bookworm-slim AS web-dependencies
WORKDIR /workspace/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM web-dependencies AS web-builder
ENV NEXT_PUBLIC_API_URL="" NEXT_PUBLIC_DEMO_MODE=false \
    NEXT_PUBLIC_MAP_STYLE_URL=/styles/community-osm.json \
    API_INTERNAL_URL=http://127.0.0.1:18000 NEXT_TELEMETRY_DISABLED=1
COPY apps/web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000 \
    ENVIRONMENT=production ADMIN_AUTH_MODE=oidc DEMO_MODE=false ALLOW_FIXTURE_INGESTION=false
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=web-dependencies /usr/local/bin/node /usr/local/bin/node
WORKDIR /workspace/apps/api
COPY apps/api/ ./
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY --from=web-builder /workspace/apps/web/.next/standalone /workspace/apps/web/
COPY --from=web-builder /workspace/apps/web/.next/static /workspace/apps/web/.next/static
COPY --from=web-builder /workspace/apps/web/public /workspace/apps/web/public
COPY scripts/hostless_runtime.py /workspace/scripts/hostless_runtime.py
COPY data/ /workspace/data/
RUN groupadd --gid 10001 heatshift && useradd --uid 10001 --gid heatshift --no-create-home heatshift \
    && chown -R heatshift:heatshift /workspace
USER heatshift
EXPOSE 3000
CMD ["python", "/workspace/scripts/hostless_runtime.py"]
