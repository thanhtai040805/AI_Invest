# AIInvest core deployment

This runbook deploys the core application on a Linux host with Docker Compose. SAG is under an active research hold and is excluded from the default Compose profile. Do not enable the `research` profile for a production deploy. This phase is Shadow only: no real brokerage orders are sent.

**Release status (2026-09-23): not signed off for PROD.** Local builds, focused Shadow checks, and a clean-volume core Compose smoke have passed. Repository-wide frontend lint still fails in legacy pages, and a fresh live market-data session with a configured paper account has not been verified. Complete those gates on the target environment before calling the Shadow deployment production ready.

## Prerequisites

- A Linux host with Docker Engine and the Compose plugin, enough capacity for PostgreSQL, Redis, RabbitMQ, AI Engine, backend, and Next.js.
- A domain whose DNS A/AAAA record points to the host. Open inbound TCP 80 and 443.
- A clean PostgreSQL volume for first deployment, or a verified backup and migration plan for an existing database. The `migrate` job runs `prisma migrate deploy` before AI Engine or backend starts.
- The migration that retires `business_quality_profiles` and `moat_profiles` refuses to run while either table contains rows. Archive and verify those rows before applying it to an existing database.
- All deployment files present in the commit being deployed. Local untracked files are not included by `git clone` or `git pull`.

## First deployment

```bash
git clone https://github.com/thanhtai040805/AI_Invest.git /opt/aiinvest
cd /opt/aiinvest
cp .env.production.example .env
chmod 600 .env
```

Edit `.env`. Set `DB_PASSWORD`, `REDIS_PASSWORD`, `RABBITMQ_PASS`, `JWT_ACCESS_SECRET`, `JWT_REFRESH_SECRET`, `AI_ENGINE_ADMIN_TOKEN`, and `INTERNAL_SERVICE_TOKEN` to independent random hex strings. For example, `openssl rand -hex 32`. Set `CORS_ORIGIN=https://your-domain.example`. Leave SAG out of this deploy. `NEXT_PUBLIC_*` values use same-origin paths, so no domain is baked into the frontend image.

Compose rejects missing required values. Do not put placeholder secrets in `.env`, and do not commit it. If a password is changed on a host with an existing PostgreSQL, Redis, or RabbitMQ volume, change the corresponding service credential as part of a planned rotation; changing `.env` alone does not update an existing database user.

Obtain a certificate before starting Nginx. Port 80 must be free for this initial standalone challenge:

```bash
sudo apt-get install -y certbot
sudo certbot certonly --standalone -d your-domain.example
mkdir -p nginx/certs
sudo install -m 0644 /etc/letsencrypt/live/your-domain.example/fullchain.pem nginx/certs/fullchain.pem
sudo install -m 0600 /etc/letsencrypt/live/your-domain.example/privkey.pem nginx/certs/privkey.pem
docker compose config -q
docker compose up -d --build --wait
```

The private key in `nginx/certs` and `.env` are ignored by Git. Nginx serves HTTPS on 443 and redirects HTTP on 80. Compose starts PostgreSQL, Redis, RabbitMQ, then a one-time Prisma migration job, AI Engine, backend, and frontend. Database and broker ports are not published on the host.

## Verify

```bash
docker compose ps
curl -fsSI https://your-domain.example/
curl -fsS https://your-domain.example/api/health
curl -fsS -o /dev/null -w '%{http_code}\n' https://your-domain.example/api/v1/market/indices
docker compose logs --tail=100 backend ai-engine frontend nginx
```

The first two requests must succeed over a valid certificate, `/api/health` must report `status: ok`, the seven long-running services must be healthy, and `migrate` must exit successfully. The market endpoint must return HTTP 200; an empty index list is expected on a fresh database. Verify a login and a representative read operation before exposing the site to users. Neither health endpoint proves external market data, LLM, or trading readiness.

Portfolio automation is disabled on first deployment. Create a dedicated paper user, put its returned ID in `MULTI_AGENT_ACCOUNT_ID`, set `PORTFOLIO_AUTOMATION_ENABLED=true`, then recreate AI Engine with `docker compose up -d --no-deps ai-engine`. Do this only after checking the account and input data. The Standalone ML channel creates its separate paper account on first use; verify that creation succeeded before relying on its orders.

Shadow fills need a live, timestamped DNSE market-data order book during a continuous trading session. `DNSE_ENABLED=false` is suitable for smoke tests, but it means no Shadow order can fill. To evaluate Shadow fills, configure only the market-data credentials on the host and set `DNSE_ENABLED=true`; the active order paths call the local paper ledger and do not call the broker's `post_order` API. Never enter account secrets into the web UI or source control. Confirm `/api/v1/stock/HPG/orderbook` has nonempty bids/asks, a current `lastUpdate`, and `marketState` equal to `continuous_morning` or `continuous_afternoon` before judging execution. If the feed is unavailable or more than 10 seconds old, orders are refused.

The current Shadow model consumes displayed depth for an entire LO/MP order and charges a modeled 0.10% fee (minimum 10,000 VND) plus 0.10% sell tax. It deliberately refuses ATO/ATC and insufficient depth. Displayed depth is not guaranteed liquidity: exchange queue position, hidden orders, partial fills, order cancellation, holidays, and broker reconciliation are not modeled. Do not treat Shadow PnL as verified live execution performance.

## Certificate renewal

The initial certificate was issued with Certbot standalone, so stop Nginx briefly for renewal. From `/opt/aiinvest`:

```bash
docker compose stop nginx
sudo certbot renew --standalone
sudo install -m 0644 /etc/letsencrypt/live/your-domain.example/fullchain.pem nginx/certs/fullchain.pem
sudo install -m 0600 /etc/letsencrypt/live/your-domain.example/privkey.pem nginx/certs/privkey.pem
docker compose up -d nginx
curl -fsSI https://your-domain.example/
```

Schedule this maintenance before certificate expiry. The renewal causes a short web outage. If uninterrupted renewal is required, add a webroot ACME challenge flow and test it before changing this procedure.

## Updates and recovery

Back up PostgreSQL before applying new migrations. Review migration SQL and test it against a restored copy when updating an existing database. Then deploy the reviewed commit and run `docker compose up -d --build --wait`. Compose may recreate containers, so plan a maintenance window. If a migration fails, the backend does not start; inspect `docker compose logs migrate` and restore from the verified backup if rollback is required. A code rollback alone does not reverse a database migration.
