FROM node:22-alpine AS dependencies
WORKDIR /workspace/apps/web
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm install

FROM node:22-alpine AS builder
WORKDIR /workspace/apps/web
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_MAP_STYLE_URL
ARG NEXT_PUBLIC_DEMO_MODE
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_MAP_STYLE_URL=$NEXT_PUBLIC_MAP_STYLE_URL \
    NEXT_PUBLIC_DEMO_MODE=$NEXT_PUBLIC_DEMO_MODE \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencies /workspace/apps/web/node_modules ./node_modules
COPY apps/web/ ./
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /workspace/apps/web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
COPY --from=builder --chown=nextjs:nodejs /workspace/apps/web/package.json ./package.json
COPY --from=builder --chown=nextjs:nodejs /workspace/apps/web/node_modules ./node_modules
COPY --from=builder --chown=nextjs:nodejs /workspace/apps/web/.next ./.next
COPY --from=builder --chown=nextjs:nodejs /workspace/apps/web/public ./public
COPY --from=builder --chown=nextjs:nodejs /workspace/apps/web/app ./app
COPY --from=builder --chown=nextjs:nodejs /workspace/apps/web/components ./components
COPY --from=builder --chown=nextjs:nodejs /workspace/apps/web/lib ./lib
COPY --from=builder --chown=nextjs:nodejs /workspace/apps/web/next-env.d.ts /workspace/apps/web/tsconfig.json ./
USER nextjs
EXPOSE 3000
CMD ["npm", "run", "start"]
