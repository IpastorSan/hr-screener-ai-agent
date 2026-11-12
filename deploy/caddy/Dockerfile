# Caddy with github.com/mholt/caddy-ratelimit, for the `rate_limit` directive on the
# Langfuse and Grafana logins (observability/caddy/observability.caddy). CI builds it and
# pushes it to Artifact Registry next to the api and web images (.github/workflows/deploy.yml);
# compose.local.yaml builds it locally. The Caddyfile stays a bind mount, not part of the image.
FROM caddy:2.10.2-builder AS builder
# Commit 5625512 (2026-06-12): the only tag, v0.1.0, predates the metrics reload fix.
RUN xcaddy build --with github.com/mholt/caddy-ratelimit@v0.1.1-0.20260612195517-5625512f24f6

FROM caddy:2.10.2-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
