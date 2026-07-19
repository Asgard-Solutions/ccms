# Setup guide — ChiroPro blue/green on the Hostinger VPS

One-time setup. After it's done, every push to `main` deploys with zero downtime.

Prereqs: SSH (root/sudo) to the Hostinger VPS, DNS control for
`adjustpro.io`, admin on the ChiroPro GitHub repo (the one with
`backend/` + `frontend/`).

---

## 1. Point DNS at the VPS
| Type | Name | Value           |
|------|------|-----------------|
| A    | @    | <VPS public IP> |
| A    | www  | <VPS public IP> |

Verify: `dig +short adjustpro.io`.

> Same VPS as SilvertreeSolutions.co. This adds a new Nginx *site* block + new
> containers on **separate ports** — it never edits Silvertree.

## 2. Prepare the app
- Backend: ensure `/api/health` exists (docs/HEALTHCHECK.md).
- Frontend: confirm `frontend/src/api/client.js` uses `REACT_APP_BACKEND_URL`.

## 3. Add files to the ChiroPro repo
Commit `deploy/` and `.github/workflows/deploy.yml` to the repo root.

## 4. Bootstrap the VPS (once)
Get the repo onto the VPS (clone or scp the `deploy/` folder), then:
```bash
sudo bash deploy/scripts/init-vps.sh
```
Installs Docker/Nginx/certbot if missing; creates `/opt/chiropro`; starts shared
**MongoDB + Redis** + the `chiro_net` network; installs the Nginx switch file +
a temporary HTTP site.

## 5. Fill in secrets on the VPS
Edit `/opt/chiropro/.env` (created from `.env.example`):
- `MONGO_ROOT_USER/PASSWORD`, `MONGO_URL`, `DB_NAME`
- `REDIS_PASSWORD`, `REDIS_URL`
- **ALL** backend app secrets (JWT, encryption/key-manager keys, MFA, object
  storage, etc.) — copy them from the backend's own `.env`. Missing ones crash boot.
- `CORS_ORIGINS`, `PUBLIC_URL`
- `MIGRATE_CMD` (leave empty until you confirm the seed entrypoint)

## 6. Issue TLS + enable HTTPS site
```bash
sudo certbot certonly --webroot -w /var/www/certbot -d adjustpro.io -d www.adjustpro.io
sudo ln -sf /etc/nginx/sites-available/adjustpro.io.conf /etc/nginx/sites-enabled/adjustpro.io.conf
sudo nginx -t && sudo systemctl reload nginx
```

## 7. GitHub secrets (repo → Settings → Secrets and variables → Actions)
| Secret        | Value                                                         |
|---------------|---------------------------------------------------------------|
| `VPS_HOST`    | VPS public IP/hostname                                        |
| `VPS_USER`    | SSH user (docker access + nginx reload — see note)           |
| `VPS_SSH_KEY` | Private SSH key (PEM); public key in VPS `authorized_keys`    |
| `VPS_PORT`    | SSH port (optional, default 22)                              |
| `GHCR_PAT`    | GitHub PAT with `read:packages` so the VPS can pull images   |

CI pushes images with the built-in `GITHUB_TOKEN`; the VPS pulls using `GHCR_PAT`.

### Permissions for switch.sh / nginx / docker
`switch.sh` writes `/etc/nginx/conf.d/chiropro-active.conf` + reloads Nginx;
`deploy.sh` runs docker. Easiest: run the SSH deploy user as root (matches many
Hostinger setups), or grant scoped passwordless sudo, e.g. `/etc/sudoers.d/chiropro`:
```
deployuser ALL=(root) NOPASSWD: /usr/sbin/nginx, /bin/systemctl reload nginx
```
and add `deployuser` to the `docker` group.

## 8. First deploy
Push to `main` (or run **Deploy ChiroPro** manually). CI builds backend +
frontend, deploys **blue**, health-checks both tiers, flips Nginx. Visit
`https://adjustpro.io`.

## 9. Rollback (anytime)
```bash
sudo bash /opt/chiropro/scripts/rollback.sh
```

---

## Day-2 ops
- Live colour: `cat /opt/chiropro/.active_color`
- Logs: `docker logs -f chiropro_backend_blue` / `chiropro_frontend_blue`
- Mongo backup: see `deploy/db/README.md`
- Redeploy an old build: run the workflow with `backend_tag` / `frontend_tag`.
