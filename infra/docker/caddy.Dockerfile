# The official image registry currently trails the upstream security release.
# Keep the official Alpine runtime and replace its executable with the pinned,
# checksum-verified upstream release. No third-party modules are installed.
FROM caddy:2.11.8-alpine
ARG TARGETARCH
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) checksum=5b8cb7faf8726a2028ad96a66e54e6e81e8f361bc6175e5d22bbd997cc5177fb ;; \
      arm64) checksum=5e377cff994fc9362a6fcf695c53f226cfca3a3ef85bd3a3687cf252d6310165 ;; \
      *) echo "Unsupported release architecture" >&2; exit 1 ;; \
    esac; \
    wget -qO /tmp/caddy.tar.gz "https://github.com/caddyserver/caddy/releases/download/v2.11.9/caddy_2.11.9_linux_${TARGETARCH}.tar.gz"; \
    echo "$checksum  /tmp/caddy.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/caddy.tar.gz -C /usr/bin caddy; \
    rm /tmp/caddy.tar.gz; \
    caddy version
LABEL org.opencontainers.image.version="2.11.9"
