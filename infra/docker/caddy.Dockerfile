# Official release image verified on 8 November 2026. Pin the published
# manifest instead of fetching a release archive during every CI build.
FROM caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee
RUN caddy version
LABEL org.opencontainers.image.version="2.11.9"
