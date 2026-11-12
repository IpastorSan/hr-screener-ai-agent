# Deploy: stage 1

One Compute Engine VM runs the whole product with Docker Compose. Caddy terminates TLS and routes `/api/*`, `/v1/*` (the voice custom LLM), `/healthz`, `/readyz`, `/docs` and `/openapi.json` to the API (FastAPI, SQLite) and everything else to the web app (Next.js). Every push to `main` runs `make check`, builds the API and web images tagged with the commit SHA and the Caddy image (`caddy/Dockerfile`: stock Caddy plus the rate-limit plugin) tagged with its Dockerfile's hash, pushes them to Artifact Registry, and restarts the VM's stack on them (`.github/workflows/deploy.yml`). Until the `production` environment has an `SSH_HOST` secret, the deploy job is skipped with a notice and CI stays green.

## 1. Google Cloud (once)

Region `europe-southwest1` (Madrid). Reserve the static IP before anything points at it.

```sh
P=<project-id>; R=europe-southwest1; Z=europe-southwest1-a
gcloud config set project $P
gcloud services enable compute.googleapis.com artifactregistry.googleapis.com iamcredentials.googleapis.com

gcloud compute addresses create sazon-ip --region $R
gcloud compute addresses describe sazon-ip --region $R --format='value(address)'

gcloud iam service-accounts create sazon-vm
gcloud compute instances create sazon --zone $Z --machine-type e2-standard-2 \
  --image-family debian-12 --image-project debian-cloud --boot-disk-size 30GB \
  --address sazon-ip --tags sazon-web \
  --service-account sazon-vm@$P.iam.gserviceaccount.com --scopes cloud-platform
gcloud compute firewall-rules create sazon-web --allow tcp:80,tcp:443,udp:443 --target-tags sazon-web

gcloud artifacts repositories create sazon --repository-format docker --location $R

gcloud storage buckets create gs://$P-sazon-backups --location $R --uniform-bucket-level-access
echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}}]}' > lifecycle.json
gcloud storage buckets update gs://$P-sazon-backups --lifecycle-file lifecycle.json
# Write and read, never delete: a compromised VM cannot wipe the off-machine copies.
for role in roles/storage.objectCreator roles/storage.objectViewer; do
  gcloud storage buckets add-iam-policy-binding gs://$P-sazon-backups \
    --member serviceAccount:sazon-vm@$P.iam.gserviceaccount.com --role $role
done
```

**Machine size.** `e2-standard-2` (2 vCPU, 8 GB) fits the API (about 750 MB with the embedding model), the web app and Caddy. With the observability stack on the same VM (section 6), use `e2-standard-4` (4 vCPU, 16 GB) and a 50 GB disk: measured idle it adds about 3.5 GB, and its memory limits add up to 10 GB.

**CI identity.** A `sazon-deployer` service account with `roles/artifactregistry.writer` on the `sazon` repository. Prefer workload identity federation for this GitHub repository and set `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_SERVICE_ACCOUNT`; a JSON key in the `GCP_SA_KEY` secret also works. The VM needs no registry access: each deploy logs it in with a short-lived token and logs it out again.

## 2. VM bootstrap (once)

```sh
gcloud compute ssh sazon --zone $Z
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"              # this user becomes SSH_USER
sudo install -d -o "$USER" /opt/sazon
# The containers run as uid 1000. Docker would create a missing bind source as root, and the
# API then crash-loops with what looks like a corrupt database.
sudo install -d -o 1000 -g 1000 /opt/sazon/data /opt/sazon/data/backups
```

Create a deploy key and add its public half to `~/.ssh/authorized_keys` on the VM:

```sh
ssh-keygen -t ed25519 -f sazon-deploy -N '' -C github-deploy   # private key -> SSH_KEY
# ECDSA, not ed25519: the deploy action's SSH client (Go x/crypto) prefers the ECDSA host key
ssh-keyscan -t ecdsa <static-ip> | ssh-keygen -lf -            # SHA256:... -> SSH_HOST_FINGERPRINT
```

Port 22 stays open to the internet for the GitHub runners, which have no fixed addresses. Keep password login off (the Debian image default).

## 3. DNS

An `A` record from the chosen host name (for example `sazon.example.com`) to the static IP. Wait until it resolves before the first deploy: Caddy requests the Let's Encrypt certificate on its first start.

## 4. GitHub `production` environment

Settings, Environments, `production`. Everything below lives there.

| Secret | |
|---|---|
| `SSH_HOST` | The static IP. Its absence is what keeps deploys switched off. |
| `SSH_USER` | The bootstrap user (in the `docker` group, owns `/opt/sazon`). |
| `SSH_KEY` | Private deploy key. |
| `SSH_HOST_FINGERPRINT` | Pins the VM's host key. The deploy refuses to run without it. |
| `OPENROUTER_API_KEY` | Production key. Zero data retention is enforced in code. |
| `SESSION_SECRET` | Signs recruiter sessions; at least 32 characters (`python -c "import secrets; print(secrets.token_urlsafe(48))"`). Changing it signs everyone out. |
| `VOICE_LLM_SECRET`, `ELEVENLABS_API_KEY` | Optional: the voice channel (see Voice below). |
| `GOOGLE_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY` | Optional: Google sign-in and Calendar (see Google sign-in and Calendar below). The API refuses to boot with the secret and no key. |
| `SITE_AUTH_HASH` | Optional: the shared site login's password, as a bcrypt hash (see Site login below). |
| `GCP_SA_KEY` | Only without workload identity federation. |

| Variable | |
|---|---|
| `GCP_PROJECT_ID`, `GCP_REGION` (`europe-southwest1`), `AR_REPOSITORY` (`sazon`) | Where the images go. |
| `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT` | Workload identity federation. |
| `DOMAIN` | The host name, without `https://`. |
| `ACME_EMAIL` | Let's Encrypt expiry notices. |
| `MAX_DAILY_LLM_USD` | Model spend per UTC day. Past it, new chats get a come-back-later message and open chats finish on templates. |
| `GCS_BACKUP_BUCKET` | For example `<project-id>-sazon-backups`. |
| Optional: `SITE_AUTH_USER` | The shared site login's user name. With `SITE_AUTH_HASH`, it gates the site; both empty leave it open, and the deploy warns. |
| Optional: `GOOGLE_CLIENT_ID`, `GOOGLE_WORKSPACE_DOMAIN`, `SCHEDULING_SOURCE` (`fixed`), `INTERVIEW_BUFFER_MINUTES` (10), `ONSITE_ORGANIZER_EMAIL` | Google sign-in and Calendar. `SCHEDULING_SOURCE=google` needs the client id, secret and `GOOGLE_WORKSPACE_DOMAIN`, and in-person interviews need `ONSITE_ORGANIZER_EMAIL` (see [Google sign-in and Calendar](#google-sign-in-and-calendar)). |
| Optional: `OPENROUTER_BASE_URL`, `RETENTION_DAYS` (365), `RATE_LIMIT_CONVERSATIONS_PER_HOUR` (20 per IP), `RATE_LIMIT_MESSAGES_PER_MINUTE` (60 per IP), `LLM_EXTRACTOR`, `LLM_RESPONDER`, `LLM_SUMMARIZER`, `LLM_ASSISTANT`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_VOICE_ES`, `ELEVENLABS_VOICE_MX`, `ELEVENLABS_VOICE_EN` | Empty means the code's default. `ELEVENLABS_AGENT_ID` is also built into the web image; empty keeps `/apply/voice` on its "not available" screen. |

Once `SSH_HOST` is set, a deploy with any required value empty fails before it touches the VM, and the VM checks again before writing `.env`. Repository variable `DEPLOY_FREEZE=true` stops all deploys, for example on an event day.

## 5. First deploy

Push to `main`, or run **Deploy** from the Actions tab. The job copies `deploy/` to `/opt/sazon`, writes `/opt/sazon/.env` (mode 600) from the environment, pulls the SHA-tagged images, starts the stack, waits for `/readyz`, and then checks `https://$DOMAIN/readyz`, `/apply` and `/recruiters` from the runner: 200, or 401 behind the site login, which also proves the login is on. When `/readyz` does not go green, it puts the previous images back.

Then install the timers on the VM:

```sh
sudo cp /opt/sazon/systemd/sazon-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sazon-backup.timer sazon-daily.timer
```

`sazon-backup` runs at 02:30 UTC. `sazon-daily` runs at 03:30 UTC: it deletes applications idle for longer than `RETENTION_DAYS` with their calendar events (`sazon purge`), creates any interview event a Google outage left out and deletes any a cancellation could not (`sazon sync-events`), and renews the Google Calendar push channels expiring within 48 hours (`sazon calendar-watch renew`). Interview times need no seeding: video ones come from the recruiters' working hours (and calendars), in-person ones from each location's interview hours in `backend/src/sazon/agents/riders/data/service_areas.json`. Demo candidates, if wanted: `docker compose exec -T api sazon seed --n 60 </dev/null`.

`/readyz` answers 200 when the database is reachable and at the latest migration and the retrieval index is loaded, 503 otherwise. Anyone can reach it, so its body is `{"status": "ok"}` or `{"status": "fail"}` alone: the API log names the failing check, and the day's spend and the cap are the Prometheus gauges `sazon_spend_today_usd` and `sazon_spend_cap_usd`. OpenRouter is not part of readiness, because the chat degrades to template replies without it and a provider blip during a deploy must not roll it back; `/readyz?deep=1` adds a check that OpenRouter's `/models` answers (at most once a minute). `/healthz` only says the process is up; the container healthcheck uses it.

## 6. Observability

`observability.compose.yaml` adds, on the same VM and compose network, with nothing hosted by a third party:

| Service | Image | Does | Memory limit (idle use) |
|---|---|---|---|
| `langfuse-web`, `langfuse-worker` | `langfuse/langfuse:4.57.0`, `langfuse-worker:4.57.0` | LLM traces: one per candidate message, chat start, nudge and recruiter question | 1.5 GB each (0.9, 0.6) |
| `langfuse-clickhouse` | `clickhouse-server:25.12.11.4` | Trace storage | 3 GB (0.8) |
| `langfuse-postgres`, `langfuse-redis`, `langfuse-minio` | `postgres:17.6-alpine`, `redis:7.4.11-alpine`, Chainguard MinIO by digest | Langfuse metadata, queue, raw events | 512, 256, 512 MB (0.1, 0.02, 0.1) |
| `prometheus` | `prom/prometheus:v3.15.0` | Scrapes the API's `/metrics` every 15 s, 15 days | 512 MB (0.1) |
| `blackbox` | `prom/blackbox-exporter:v0.29.0` | Probes `/readyz` from inside the network | 64 MB |
| `loki`, `alloy` | `grafana/loki:3.7.8`, `grafana/alloy:v1.20.1` | Container logs, 30 days | 512, 256 MB (0.2, 0.1) |
| `grafana` | `grafana/grafana:13.2.4` | Dashboard "Sazón — operación" and the alerts | 1 GB (0.5) |

Langfuse and Grafana get their own host names through Caddy, with automatic HTTPS and their own logins (signup off in both). `/metrics` is never routed by Caddy (it answers 404 there); Prometheus reads it on the compose network. Langfuse's and Grafana's own usage telemetry is off.

**Login rate limits.** Caddy allows each client IP 10 login POSTs per minute per site and answers the rest 429 with `Retry-After`: on Langfuse, `POST /api/auth/callback/credentials` and `/api/auth/signin` (NextAuth, which has no limit of its own); on Grafana, `POST /login`. Page loads, the rest of both apps and Langfuse's OTLP endpoint are not limited (the API sends traces over the compose network, not through Caddy). The `rate_limit` directive comes from `github.com/mholt/caddy-ratelimit`, pinned in `caddy/Dockerfile`; CI builds that image and the VM pulls it like the others, and `make stack-up` builds it locally. Grafana also keeps its built-in lockout, which the stack leaves on: 5 failed logins within 5 minutes lock that user for 5 minutes (`disable_brute_force_login_protection` is not set). The main site's `/api/auth/*` has the API's own per-IP and per-email login limits, so Caddy does not limit it.

**Turn it on (once).** Two more `A` records (for example `langfuse.example.com` and `grafana.example.com`) to the static IP, then on the VM:

```sh
cd /opt/sazon
h() { openssl rand -hex "${1:-24}"; }
cat > observability.env <<ENV
LANGFUSE_DOMAIN=langfuse.example.com
LANGFUSE_URL=https://langfuse.example.com
GRAFANA_DOMAIN=grafana.example.com
GRAFANA_URL=https://grafana.example.com
LANGFUSE_PUBLIC_KEY=pk-lf-$(h 16)
LANGFUSE_SECRET_KEY=sk-lf-$(h 16)
LANGFUSE_ADMIN_EMAIL=you@example.com
LANGFUSE_ADMIN_PASSWORD=$(h)
LANGFUSE_NEXTAUTH_SECRET=$(h)
LANGFUSE_SALT=$(h)
LANGFUSE_ENCRYPTION_KEY=$(h 32)
LANGFUSE_POSTGRES_PASSWORD=$(h)
LANGFUSE_CLICKHOUSE_PASSWORD=$(h)
LANGFUSE_REDIS_PASSWORD=$(h)
LANGFUSE_MINIO_PASSWORD=$(h)
GRAFANA_ADMIN_PASSWORD=$(h)
ALERT_WEBHOOK_URL=
ALERT_EMAIL=
ENV
chmod 600 observability.env
```

`observability.env.example` lists the same variables, with SMTP for email alerts. The next deploy appends the file to `.env` together with `COMPOSE_FILE=compose.yaml:observability.compose.yaml`, so every `docker compose` on the VM (the deploy, `backup.sh`, the timers) includes the stack. The first start takes a couple of minutes while Langfuse migrates its databases; Langfuse then creates the organization, the `sazon-screener` project, its API keys and the admin user from the file, so nothing is set up by hand. The admin passwords are in `observability.env`. Deleting the file turns the stack off on the next deploy (its volumes stay).

**Tracing.** The API exports OpenTelemetry over OTLP/HTTP to `http://langfuse-web:3000/api/public/otel` with the project keys as basic auth. Without that endpoint (tests, `make dev-api`, the plain stack) tracing is off and costs nothing. Every trace carries the conversation as its session, a hashed candidate id as its user, tags for country, language, channel and outcome, and `prompt_version`, a hash of the prompt files. Spans: `sanitize`, `extract` (with Pydantic AI's agent and model spans: messages, tokens, cost), `engine.step`, `fill_action` (`.duplicate`, `.slots`, `.faq`), `respond`, `persist`. Before export, emails and phone numbers are masked in every span, and so are the candidate's name, phone and email as the extractor read them. `TRACE_CONTENT=false` keeps prompts, replies and candidate text out of traces. Open-source Langfuse has no retention setting, so `langfuse-retention.sh`, run by `sazon-daily`, deletes traces older than 30 days (`TRACE_RETENTION_DAYS`) and sets a 30-day expiry on the MinIO bucket.

**Metrics** (`sazon_*` on `/metrics`): `turns_total{role}`, `llm_calls_total{role,model}`, `llm_latency_seconds{role,model}`, `llm_fallback_total{role}`, `llm_cost_usd_total{role,model}` (model calls only: a reply sent as written by design counts in `fixed_replies_total{role}` instead, so it neither dilutes the fallback rate nor pulls the latency percentiles towards 0), `conversations_started_total{country,channel}`, `conversation_outcomes_total{outcome}`, `quality_flags_total{flag}`, `faq_miss_total`, `nudges_served_total{attempt}`, `nudge_recoveries_total`, `openrouter_errors_total{status}`, `spend_cap_blocks_total`, `rate_limited_total{bucket}`, `spend_today_usd`, `spend_cap_usd`, `backup_newest_timestamp_seconds`, `calendar_notifications_total{state}` (Google Calendar push notifications: `sync`, `exists`, `rejected`...), `calendar_reconciliations_total{type}` (`moved`, `moved_flagged`, `cancelled`, `reassigned`, `recruiter_declined`, `manager_declined`, `candidate_declined`, `resync`), `calendar_watch_failures_total{op}`, `calendar_channels{state}` (connected calendars whose push channel is `ok`, `expiring` or `missing`), plus `http_*` per route.

**Logs.** With `ENV=production` the API logs one JSON object per line (`ts`, `level`, `logger`, `msg`, and `conversation_id`, `trace_id`, `span_id` inside a turn), masked again on the way out. Alloy ships every container's logs to Loki with `service`, `container` and (for the API) `level` labels. In Grafana's Explore: `{service="api"} | json | conversation_id="<id>"`. `LOG_FORMAT=text|json` overrides the default.

**Alerts** (Grafana, provisioned from `observability/grafana/provisioning/alerting/`): fallback rate over 5% of model calls in 15 minutes (with at least 10 calls); responder p95 over 4 s for 10 minutes; spend over 80% of `MAX_DAILY_LLM_USD`; any OpenRouter 402 or 403 (OpenRouter answers 403 "Key limit exceeded" when the key's own limit runs out); 429 over 2% of calls for 10 minutes; `injection_suspected` flags in the last hour over 3 times the 7-day hourly average (and at least 3); `/readyz` failing for 2 minutes; newest local backup older than 26 hours; for 30 minutes, a connected Google Calendar without a push channel or with one under 24 hours from expiry (the daily renewal failed or did not run). All go to the `sazon-ops` contact point: `ALERT_WEBHOOK_URL` and `ALERT_EMAIL` (email needs `GRAFANA_SMTP_*`).

**Locally.** `make obs-up` runs the stack of `make stack-up` plus all of the above, with Langfuse on http://localhost:3100 (`admin@example.com` / `local-admin-password`) and Grafana on http://localhost:3200 (`admin` / `local-admin`); `make obs-down` removes it with its volumes. The local API uses the offline model, so traces show the turn's spans without model calls; to see generations, give the `api` service `SAZON_FAKE_LLM=false` and an `OPENROUTER_API_KEY` in an uncommitted compose override.

**The dev API too.** Locally, `alloy-dev` (`observability-dev.alloy`, host network) scrapes `make dev-api` on 127.0.0.1:8000 as job `api-dev` and tails `backend/logs/dev-api.log` (JSON, which `make dev-api` writes) as service `api-dev`. Containers cannot reach the host's loopback past the firewall, so `alloy-dev` pushes instead, to Prometheus (remote-write receiver) and Loki published on 127.0.0.1:9090 and 127.0.0.1:3110. The dashboard's "API" variable picks `api`, `api-dev` or both. Production runs none of this.

**On a second VM.** The file keeps observability apart from the app so it can move: run it there with its own Caddy, point `OTEL_EXPORTER_OTLP_ENDPOINT` at that VM's Langfuse host name, and have Prometheus scrape the API over the private network instead of `api:8000`.

## Site login

Until the screener opens to the public, one shared user name and password (HTTP basic auth in Caddy) gate the whole main site: the careers pages, the chat, the voice page, the dashboard and its API. The browser asks once and resends it for every request to the site, including the Google sign-in callback and the API calls of the chat and the voice page. Open without it, because machines call them with their own credentials: `/v1/*` (ElevenLabs' custom LLM, `VOICE_LLM_SECRET`), `/api/webhooks/*` (Google Calendar push, channel tokens), `/healthz` and `/readyz`. Langfuse and Grafana keep their own logins only.

```sh
docker run --rm -it caddy:2.10.2-alpine caddy hash-password   # prompts twice; prints $2a$14$...
```

Set the `SITE_AUTH_USER` variable (letters, digits, `. _ @ -`) and the `SITE_AUTH_HASH` secret to the printed hash, as is, and redeploy. Share the user name and the password, never the hash. The deploy refuses one without the other and anything that is not a bcrypt hash, and writes the hash single-quoted into `.env`, so Compose keeps its `$` signs literally. To change the password, set a new hash and redeploy; to open the site, delete both and redeploy. Locally both are empty and nothing is gated.

## Rollback

Run **Deploy** by hand with `sha` set to the full SHA of an earlier `main` commit. It skips the checks and the build, pins that commit's API and web images in `.env` (Caddy stays on the image built for the current Caddyfile), and lasts until the next push to `main`. Migrations only move forward, so an older image must still work with the newer schema; when one does not, restore the backup taken before the deploy instead.

## Backups and restore

`backup.sh` makes an online copy inside the API container (`sazon backup`, the same copy as sqlite3's `.backup`, followed by `PRAGMA integrity_check`), gzips it, uploads it to `gs://$GCS_BACKUP_BUCKET/sqlite/`, and keeps the newest 14 in `/opt/sazon/data/backups`. The bucket deletes copies after 30 days. Run one by hand with `sudo /opt/sazon/backup.sh`.

The SQLite copy is the only backup. `observability.env` (Langfuse's encryption key, the admin passwords) and the Langfuse, Prometheus, Loki and Caddy volumes stay on the VM: losing the VM means a new `observability.env`, no past traces, metrics or logs, and new TLS certificates.

```sh
cd /opt/sazon
gcloud storage ls gs://$GCS_BACKUP_BUCKET/sqlite/ | tail -n 3
sudo ./restore.sh gs://$GCS_BACKUP_BUCKET/sqlite/sazon-20261020T023000Z.db.gz
```

`restore.sh` stops the API, checks the copy, keeps the current database as `data/sazon.db.before-restore-<time>`, swaps the copy in, and waits for the API to come back healthy.

The drill on a laptop: `make stack-up && make stack-seed && deploy/test-restore.sh`. It backs up the local stack, deletes the database, restores it, compares the row counts of every table, and checks `/readyz`.

## Voice

The voice interview (`/apply/voice`) uses ElevenLabs Agents for speech to text, text to speech and turn taking. ElevenLabs calls our API as its custom LLM: `POST https://$DOMAIN/v1/chat/completions` (OpenAI chat completions, streamed), with `VOICE_LLM_SECRET` as the API key. The screening itself runs in our engine, so a voice call stores the same transcript, fields and summary as a chat. The call page keeps one conversation per country and language, carries on only with a voice conversation of its own country, and its header's "Empezar de nuevo" ends the call (asking first while one is live) and starts a fresh application, as the chat's does. Nothing voice-related runs until these are set:

| Setting | Where | |
|---|---|---|
| `VOICE_LLM_SECRET` | API (`production` secret) | A long random string (`openssl rand -hex 32`). Empty turns `/v1/chat/completions` off (401). |
| `VOICE_LLM_URL` | API | `https://$DOMAIN/v1`. The deploy job writes it. ElevenLabs adds `/chat/completions`. |
| `ELEVENLABS_API_KEY` | API (`production` secret) | Used by `sazon voice-agent` and to mint conversation tokens: the agent is private (`enable_auth`), so the browser never gets a reusable credential. |
| `ELEVENLABS_AGENT_ID` | API and web build (`production` variable) | Printed by `sazon voice-agent create`. The web image reads it as `NEXT_PUBLIC_ELEVENLABS_AGENT_ID` at build time. |
| `ELEVENLABS_VOICE_ES`, `_MX`, `_EN` | API | Voice ids per register. Pick them in the ElevenLabs Voice Library (filter Spanish, accent Spain or Mexico, use case conversational; or `GET /v1/shared-voices?language=es&locale=es-MX&use_cases=conversational`), add them to the workspace, and paste their ids. Empty uses the agent's default. |
| `VOICE_FILLER_AFTER_S` | API | Off (0) by default and not passed by the deploy job. When a reply is not ready after this many seconds, Ana says "Vale... " first (ElevenLabs' buffer-word advice). |
| `ELEVENLABS_API_URL` | API | `https://api.eu.residency.elevenlabs.io` for the EU residency environment (Enterprise only, a separate account and key). |

**Create the agent** once the settings are in the API's environment. The agent is defined in `deploy/elevenlabs/agent.json`: custom LLM, Spanish with an English preset, Flash v2.5 voices with ElevenLabs' own text normalisation (`text_normalisation_type: elevenlabs`: the default, `system_prompt`, only asks the LLM to write numbers as words, and our custom LLM never reads the prompt; interview times already come in words), first message `{{opening_line}}` (the greeting comes from our engine, with the AI disclosure and the consent question), `end_call` (with `pre_tool_speech` off, so the agent adds nothing of its own before hanging up) and `language_detection` tools, a 7 s turn timeout, 10 minute calls, no audio recording and transcripts deleted at ElevenLabs (`retention_days: 0`).

```sh
cd /opt/sazon                                   # the image does not ship deploy/, so copy the config in
docker compose cp elevenlabs/agent.json api:/tmp/agent.json
docker compose exec -T api sazon voice-agent create --config /tmp/agent.json --dry-run </dev/null
docker compose exec -T api sazon voice-agent create --config /tmp/agent.json </dev/null   # prints agent_id
# set the ELEVENLABS_AGENT_ID variable in the production environment and redeploy (rebuilds the web image)
docker compose exec -T api sazon voice-agent update --config /tmp/agent.json </dev/null   # after editing agent.json
```

`create` stores `VOICE_LLM_SECRET` as an ElevenLabs workspace secret; `update --rotate-secret` stores it again after a rotation. `sazon voice-agent show` prints the agent as ElevenLabs has it.

**Test locally.** ElevenLabs must reach the custom LLM over public HTTPS, so expose the API with a tunnel. The URL changes every session, so update the agent each time:

```sh
make dev-api                                         # with VOICE_LLM_SECRET in backend/.env
cloudflared tunnel --url http://localhost:8000       # prints https://<random>.trycloudflare.com
VOICE_LLM_URL=https://<random>.trycloudflare.com/v1 uv run --project backend sazon voice-agent update
NEXT_PUBLIC_ELEVENLABS_AGENT_ID=<agent id> make dev-web   # then open /apply/voice
```

Without the tunnel and the update, a local page's call still connects, but its turns go to the URL the agent last had (the deployed API), which has no such conversation: Ana answers every turn with "Perdona, no encuentro tu solicitud", and the local API logs no `POST /v1/chat/completions`. The API warns when a call starts with `VOICE_LLM_URL` unset.

A private agent also needs the page's host in its allowlist; `voice-agent` sets it to the `VOICE_LLM_URL` host, so for a local test either add `localhost` in the ElevenLabs dashboard or set `"enable_auth": false` in a copy of the config.

**Data protection.** Audio is processed by ElevenLabs in the United States: EU data residency and zero retention mode are Enterprise features. The agent asks for no recording and immediate deletion of transcripts (`record_voice: false`, `retention_days: 0`); check in the dashboard that the plan honours them. Our own copy of the transcript follows `RETENTION_DAYS`, like the chat.

## Google sign-in and Calendar

Recruiters sign in with their Google Workspace account (corporate SSO), and the same grant connects their Google Calendar: video interviews are offered only when an eligible recruiter is free there, and each one becomes an event on the assigned recruiter's calendar with a Google Meet link and the candidate invited. Without these settings the dashboard keeps email and password sign-in and scheduling uses working hours only (`SCHEDULING_SOURCE=fixed`).

In-person interviews are with each location's manager, never on a recruiter's calendar: their times come from the location's interview hours (Monday to Friday 10:00-14:00 and 16:00-20:00 local time by default, one interview at a time and at most 15 a day per location; managers and hours are edited in `backend/src/sazon/agents/riders/data/service_areas.json`). Their invitations go out from one organizer account, `ONSITE_ORGANIZER_EMAIL`: an existing recruiter (in production, the admin's) whose connected calendar holds every in-person event, with the candidate and the location's manager as guests. That account can still run video interviews; its in-person events show as busy in its own free/busy. With `SCHEDULING_SOURCE=google`, in person is not offered while the organizer is unset, inactive or not connected.

**Google Cloud (once).** Any project works; it need not be the deploy one.

1. Enable the Google Calendar API: `gcloud services enable calendar-json.googleapis.com`.
2. Google Auth Platform, Branding: app name, support email, the authorized domains (the Workspace domain and the deploy domain).
3. Audience: **Internal** when the project belongs to the Workspace organization (only its accounts can sign in, no verification needed). From a project outside the organization: **External** in **Testing**, with each recruiter's account as a test user (testing grants expire after 7 days; publish the app to stop that).
4. Data access: add the scopes `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`, `.../auth/calendar.events` and `.../auth/calendar.freebusy`. `calendar.freebusy` is enough for `freeBusy.query`; nothing reads event contents.
5. Clients, Create client, type **Web application**. Authorized redirect URIs: `http://localhost:8000/api/auth/google/callback` for development and `https://$DOMAIN/api/auth/google/callback` for production. No JavaScript origins are needed: the browser never talks to Google's APIs.

| Setting | Where | |
|---|---|---|
| `GOOGLE_CLIENT_ID` | API (`production` variable) | The client's id. |
| `GOOGLE_CLIENT_SECRET` | API (`production` secret) | The client's secret. With the id and the Workspace domain, it turns on "Continuar con Google" and "Conectar Google Calendar". |
| `TOKEN_ENCRYPTION_KEY` | API (`production` secret) | Encrypts the stored refresh tokens (Fernet): `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Changing it disconnects every calendar (recruiters reconnect from the header). |
| `GOOGLE_REDIRECT_URI` | API | Must match a redirect URI of the client exactly. The deploy job writes `https://$DOMAIN/api/auth/google/callback`; the default is the local one. |
| `GOOGLE_WORKSPACE_DOMAIN` | API (`production` variable) | The client's Google Workspace domain, empty by default. Only accounts whose ID token carries this `hd` claim, with a verified email, can sign in; Google stays off without it, and production refuses to start with the client id set and no domain. |
| `SCHEDULING_SOURCE` | API (`production` variable) | `google` to schedule on the recruiters' calendars, `fixed` (default) for working hours only, `fake` for an in-memory calendar in demos (refused in production). |
| `INTERVIEW_BUFFER_MINUTES`, `SCHEDULING_DAYS` | API | Free time around each video interview (10) and how many days ahead times are offered (14). |
| `ONSITE_ORGANIZER_EMAIL` | API (`production` variable) | The recruiter account whose calendar sends the in-person invitations (the admin's in production). It must exist and connect its calendar. |

**Who can sign in.** Accounts are not created on sign-in: an admin adds the recruiter first (dashboard, Equipo, or `sazon add-recruiter`) with the same email as their Google account. A Google account outside the Workspace, with an unverified email, or without an active recruiter is turned away with a message on the sign-in page.

**Connecting calendars.** A recruiter who signs in with Google for the first time grants calendar access at the same time. One who signs in with a password connects it from the header ("Conectar Google Calendar"), with the Google account of the same email. In `google` mode only recruiters with a connected calendar are offered to candidates, so check the team page's Calendario column after switching. "Desconectar" revokes the grant at Google and deletes it; a grant Google stops accepting (password change, removed access) shows as "Reconectar".

**In-person interviews booked before the managers.** After the deploy that brings them, with `ONSITE_ORGANIZER_EMAIL` set and its calendar connected, move the upcoming in-person bookings still held by recruiters: each goes to the organizer with the location's manager, its old event leaves the recruiter's calendar quietly and a new invitation reaches the candidate and the manager. It is idempotent; check the count first.

```sh
docker compose exec -T api sazon migrate-onsite-bookings --dry-run </dev/null
docker compose exec -T api sazon migrate-onsite-bookings </dev/null
```

**Data protection.** The event holds the candidate's name, email and the interview time; Google acts as a processor (the privacy notice says so). Erasure (`sazon erase`, the dashboard's delete) and the retention purge delete the events too; an upcoming one sends the candidate a cancellation.

**Live check (development).** With the client id, secret and `TOKEN_ENCRYPTION_KEY` in `backend/.env` and `SCHEDULING_SOURCE=google`:

```sh
make dev-api && make dev-web                           # API on :8000, web on :3000
cd backend && uv run sazon add-recruiter --email you@your-workspace-domain --name You --country ES --locations es-mad-centro
# http://localhost:3000/login -> "Continuar con Google" -> consent -> back on /recruiters, header shows "Calendar conectado"
# apply at http://localhost:3000/apply as a candidate in Madrid with your own email: the slots offered skip your busy times,
# and the booking appears in your Google Calendar with the candidate invited.
```

**Two-way sync (push notifications).** With `PUBLIC_URL` set (the deploy job writes `https://$DOMAIN`), every connected calendar gets a push channel (`events.watch`) to `$PUBLIC_URL/api/webhooks/google/calendar`: when a recruiter moves, deletes or declines an interview in Google Calendar, the booking follows within seconds. Google sends the candidate its own update email; the chat shows a notice when they come back.

| Change in Google Calendar | What happens to the booking |
|---|---|
| Event deleted | Cancelled (`cancelled_by=recruiter`). The candidate's chat goes back to scheduling: a notice ("el equipo de selección ha cancelado tu entrevista...") and new times the next time they open it. |
| Event moved | The booking takes the new time and the chat says so. A time the rules would not have offered (in the past, outside the recruiter's working hours or, in person, the location's hours, over another interview or a busy block, past the daily maximum, an all-day event) is kept as the recruiter set it, but flagged on "Mis entrevistas" for them to fix. Google has already told the candidate the new time, so the booking does not contradict it. |
| Recruiter declined | A video interview goes to another recruiter free at that time (a new event on their calendar; the old one is deleted, so the candidate gets a cancellation and a new invitation). Nobody free, or an in-person one the organizer declined: as if deleted. |
| Location manager declined (in person) | Cancelled (`cancelled_by=manager`), the event deleted so Google tells the candidate; the chat says the manager cannot do it and offers new times. Nobody else takes it. |
| Candidate declined the invitation | Cancelled (`cancelled_by=candidate`), the event deleted without another email, new times offered if they come back. |

Only our events count: the ones a booking holds, which carry the booking id in `extendedProperties.private.sazonBookingId`. Notifications are answered with 200 at once and processed in the API process, one sync at a time per calendar; duplicates and notifications out of order collapse into a sync that reads the calendar as it is now. A notification whose channel id, token or resource id do not match gets 403 (Google retries only 5xx, so a forged or stale one is not sent again). The webhook is limited per IP (`RATE_LIMIT_CALENDAR_WEBHOOKS_PER_MINUTE`, 300). Channel tokens are random per channel, stored as SHA-256, and dropped from Caddy's access log.

Channels live `CALENDAR_WATCH_TTL_HOURS` (168, a week). A channel opens when a recruiter connects their calendar and is stopped when they disconnect it; `sazon-daily` replaces the ones expiring within 48 hours. By hand:

```sh
docker compose exec -T api sazon calendar-watch status </dev/null   # ok / expiring / missing per connected calendar
docker compose exec -T api sazon calendar-watch start </dev/null    # open one wherever it is missing (after first setting PUBLIC_URL)
docker compose exec -T api sazon calendar-watch renew </dev/null    # replace those expiring within 48 h (--within-hours N)
docker compose exec -T api sazon calendar-watch stop </dev/null     # close them all (--email for one recruiter)
```

If `start` fails with `401 push.webhookUrlUnauthorized` ("Unauthorized WebHook callback channel"), Google wants the webhook's domain verified: add it as a domain property in Google Search Console with an owner of the Cloud project, then run `start` again.

**Test push locally.** Google only calls public HTTPS, so expose the API with a tunnel. A quick tunnel's URL changes every session; the channels opened with it die with it, so stop them before closing the tunnel:

```sh
# backend/.env: GOOGLE_CLIENT_ID/SECRET, TOKEN_ENCRYPTION_KEY, SCHEDULING_SOURCE=google
cloudflared tunnel --url http://localhost:8000            # prints https://<random>.trycloudflare.com
export PUBLIC_URL=https://<random>.trycloudflare.com
make dev-api                                              # API on :8000 (the OAuth redirect stays localhost)
make dev-web                                              # sign in with Google and connect the calendar:
                                                          # that opens the channel; or, for one already connected:
cd backend && uv run sazon calendar-watch start           # prints "1 started"
# book an interview as a candidate (see the live check above), then in your Google Calendar:
#   drag it to another time -> within seconds the booking moves (dashboard "Mis entrevistas", the candidate's chat)
#   delete it               -> the booking is cancelled; reopening the chat shows the notice and new times
uv run sazon calendar-watch stop                          # before closing the tunnel
```

A `trycloudflare.com` address cannot be verified in Search Console; if Google refuses it, use a named tunnel on a domain you own (`cloudflared tunnel create`, a DNS route) and verify that domain.

## Locally

`make stack-up` builds both images and serves the stack at http://localhost:8080 through Caddy on plain HTTP, with the offline model (`deploy/local.env`) and the database in `deploy/data-local`. `make stack-down` removes the containers and volumes.
