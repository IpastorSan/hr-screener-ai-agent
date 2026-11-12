# syntax=docker/dockerfile:1.7
# The candidate site and the recruiter dashboard: Next.js standalone output.

FROM node:26.11.1-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --include=dev --no-audit --no-fund

FROM deps AS build
COPY . .
# The browser calls the API on the same origin (Caddy routes /api/* to it), so the API base
# is empty. Inlined at build time.
ARG NEXT_PUBLIC_API_URL=""
# The ElevenLabs agent of the voice interview; empty shows "voice not configured".
ARG NEXT_PUBLIC_ELEVENLABS_AGENT_ID=""
# The public origin, e.g. https://jobs.example.com: canonical and hreflang links are absolute.
ARG NEXT_PUBLIC_SITE_URL=""
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_ELEVENLABS_AGENT_ID=${NEXT_PUBLIC_ELEVENLABS_AGENT_ID} \
    NEXT_PUBLIC_SITE_URL=${NEXT_PUBLIC_SITE_URL} \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:26.11.1-slim AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
# The standalone server leaves .next/static and public/ (the careers photos) out; copy them in.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
