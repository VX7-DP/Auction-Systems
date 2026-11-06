# Deploying to one VM

This is the runbook for the one-VM option in CLAUDE.md section 8. The whole Compose stack runs on one Linux VM, with Caddy in front at `https://auction.summittech.in`. The files are in `deploy/`.

## Tradeoffs

- **The VM is a single point of failure.** If it goes down, the demo goes down until the VM is back. Docker restarts the stack after a reboot.
- **A redeploy restarts the backend.**
  - API requests pause for a few seconds. Caddy holds them for up to 30 s while the backend comes back, instead of answering 502.
  - Live viewers' WebSockets drop, and the clients reconnect and resync from the REST snapshot.
- **You own patching, TLS and backups.**
  - Caddy gets and renews certificates by itself.
  - Nightly dumps sit on the VM's own disk. They survive a broken database or a bad deploy, but not the loss of the VM. Turn on Lightsail automatic snapshots, or copy dumps off the VM (see [Backups](#backups)).
- **Images build on the VM.** That keeps deploys to `git pull` plus one command, but a 2 GB VM needs swap for the builds.

## What runs

| Service | Job |
| --- | --- |
| `caddy` | HTTPS, the built front end, and `/api` and `/ws` proxied to the backend. The only service with published ports (80 and 443). |
| `backend` | The FastAPI app. Only Caddy can reach it. |
| `migrate` | Runs the Alembic migrations, then exits. It runs before the backend on every `up`. |
| `postgres` | The source of truth. Data lives in the `pgdata` volume. |
| `redis` | The hot path. It has no persistence; it is rebuilt from Postgres. |
| `backup` | A `pg_dump` every night at `BACKUP_TIME` (UTC) into `BACKUP_DIR`, keeping `BACKUP_KEEP_DAYS` days. |

`deploy/docker-compose.prod.yml` sits on top of the base `docker-compose.yml`. The `.env` in the repo root sets `COMPOSE_FILE`, so plain `docker compose ...` commands use both files. The Vite dev server doesn't run in production.

## First deploy

1. **VM.**
   - Create a Lightsail instance with Ubuntu 24.04 LTS on the 2 GB plan, and attach a static IP.
   - In the Lightsail firewall, allow TCP 80 and 443 next to SSH. Also allow UDP 443 if you want HTTP/3; browsers fall back without it.
   - Rely on this firewall, not UFW: Docker-published ports bypass UFW.
2. **DNS.** Add an A record `auction.summittech.in` pointing at the static IP. Don't add an AAAA record. Wait until `dig +short auction.summittech.in` prints the IP: Caddy can't get a certificate before then.
3. **Docker.** Install Docker Engine and the Compose plugin from Docker's apt repository, following <https://docs.docker.com/engine/install/ubuntu/>. Ubuntu's own packages may ship a Compose too old for the `!reset` tag the override uses (it needs v2.24 or later). Then let your user run Docker, and log out and back in:

   ```bash
   sudo usermod -aG docker "$USER"
   ```

4. **Swap**, so the image builds fit in 2 GB:

   ```bash
   sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
   ```

   ```bash
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

5. **Code.** If the repo is private, add a read-only deploy key on GitHub and clone over SSH instead.

   ```bash
   sudo mkdir -p /opt/auction && sudo chown "$USER" /opt/auction
   ```

   ```bash
   git clone https://github.com/VX7-DP/Auction-Systems.git /opt/auction && cd /opt/auction
   ```

6. **Settings.** Copy the template, then fill in `POSTGRES_PASSWORD`, `SESSION_SECRET` and `ADMIN_PASSWORD`. Generate each with `openssl rand -hex 32`.

   ```bash
   cp deploy/.env.example .env && chmod 600 .env
   ```

7. **Start.** The first build takes a few minutes.

   ```bash
   docker compose up -d --build --wait
   ```

8. **Check.**
   - `curl -fsS https://auction.summittech.in/api/health` prints `{"status":"ok","postgres":"up","redis":"up"}`.
   - `http://auction.summittech.in` redirects to HTTPS.
   - A WebSocket upgrade works through Caddy. Run the command below with an id from `GET /api/auctions`; it should print `HTTP/1.1 101 Switching Protocols`. (Checked against the real `/ws/auctions/{id}` route with a local production stack, including a `wss://` client receiving a live update.)
   - `docker compose ps` shows every service healthy, and `backup` has written its first dump.

   ```bash
   curl -sS --http1.1 -i -N --max-time 3 -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' https://auction.summittech.in/ws/auctions/<id> | head -1
   ```

9. **AWS account.** Set a billing alert (CLAUDE.md section 8), and turn on automatic snapshots for the instance.

## Redeploy

From `/opt/auction`:

```bash
docker compose exec backup backup now
```

```bash
git pull && docker compose up -d --build --wait
```

```bash
curl -fsS https://auction.summittech.in/api/health
```

- **Migrations** run while the old backend is still serving. Keep them backward compatible: add first, and drop or rename in a later release.
- **Rollback.** Check out the previous commit and run the same `up` command. If the bad release migrated the schema, restore the dump taken just before it.
- **Disk.** Old images pile up. Clear them now and then:

  ```bash
  docker image prune -f && docker builder prune -f
  ```

## Backups

- **Schedule.** The `backup` service runs `pg_dump --format=custom` every day at `BACKUP_TIME` (UTC). It also takes a dump at startup if none is under a day old.
- **Location and retention.** Files are `BACKUP_DIR/auction-<UTC time>.dump` on the host (default `/var/backups/auction`). They are owned by root with mode 600, and deleted after `BACKUP_KEEP_DAYS` days. Old dumps are pruned only after a successful new one.
- **Take one now:** `docker compose exec backup backup now`.
- **Health signal.** `docker compose ps` shows `backup` as unhealthy when there's no dump from the last 26 hours. `docker compose logs backup` says why.
- **Off the VM.** The dumps share the VM's disk. Lightsail automatic snapshots cover the whole disk, dumps included. To keep a copy elsewhere, pull one down from your machine:

  ```bash
  ssh ubuntu@<vm> 'sudo cat /var/backups/auction/<file>' > <file>
  ```

## Restore

From `/opt/auction`, with a file name from `BACKUP_DIR`:

```bash
deploy/restore.sh auction-20261105T031500Z.dump
```

The script asks you to type `restore`, then does the following:
1. Takes a safety dump of the current database, so the restore can be undone. `SKIP_SAFETY_DUMP=1` skips it, for when the current database can't be dumped.
2. Stops the backend.
3. Drops the database, recreates it, and restores the dump in one transaction, then runs the migrations, so an older dump is brought up to the current schema.
4. Moves every auction's sequence numbers forward by `RESTORE_SEQ_JUMP` (default 1,000,000). The dump holds older numbers than the ones viewers last saw, and open pages ignore any number at or below the one they show. After the jump, each open page sees one gap and reloads the restored state. Raise it when restoring a dump much older than a day.
5. Restarts Redis. Redis is rebuilt from Postgres. Left as it was, Redis would hold sequence numbers and bids newer than the restored data. After the restart it starts empty, and each auction reloads from Postgres on its next bid.
6. Starts the backend.

**Every bid confirmed after the dump was taken is lost.**

**To restore on a new VM:** do the first deploy, copy the dump into `BACKUP_DIR`, then run the script.

## Test the production stack locally

This needs Docker, ports 80 and 443 free, and a `.env` like the one above with `SITE_ADDRESS=localhost`. With that address, Caddy uses its internal CA instead of Let's Encrypt.

```bash
docker compose up -d --build --wait
```

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
```

```bash
curl --cacert caddy-root.crt https://localhost/api/health
```

Afterwards, run `docker compose down`, delete `.env` and `caddy-root.crt`, and empty `BACKUP_DIR` (which defaults to `/var/backups/auction`). With `.env` gone, plain `docker compose` is the dev stack again.

## Troubleshooting

- **No certificate.** Check `docker compose logs caddy`. Usually DNS doesn't point at the VM yet, or ports 80 or 443 are closed in the Lightsail firewall. Caddy keeps retrying on its own.
- **502 or slow API.** The backend is down or restarting. Check `docker compose ps` and `docker compose logs backend`.
- **Disk full.** Postgres stops writing, so bidding stops (it fails closed). Check `df -h` and `docker system df`, then prune images. Container logs are capped at 50 MB per service.
