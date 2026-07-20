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

Both push (on the runner) and pull (on the VPS over SSH) authenticate to GHCR
with the built-in, short-lived `GITHUB_TOKEN` — valid only for the duration of
each run, which is exactly when the VPS pulls. **No long-lived PAT is needed.**
(Containers use `--restart unless-stopped`, so a VPS reboot restarts them from
locally-cached images without any registry login.)

### Running the deploy as a non-root `deploy` user (recommended)
The CI SSH user (`VPS_USER`) needs: Docker access, ownership of `/opt/chiropro`,
and scoped sudo for the three Nginx operations `switch.sh` performs. One-time
setup on the VPS (as root):
```bash
# 1. Create the user + give it Docker access  (or just run:
#    sudo DEPLOY_USER=deploychiro bash deploy/scripts/init-vps.sh )
sudo adduser --disabled-password --gecos "" deploychiro
sudo usermod -aG docker deploychiro

# 2. Let it own the deploy dir (but keep the app data dir as uid 1000, the
#    non-root user the backend container runs as)
sudo chown -R deploychiro:deploychiro /opt/chiropro
sudo chown -R 1000:1000 /opt/chiropro/data

# 3. Scoped passwordless sudo for ONLY the Nginx actions switch.sh needs.
#    Verify the binary paths first (distros differ):
which nginx systemctl tee
sudo tee /etc/sudoers.d/chiropro >/dev/null <<'EOF'
deploychiro ALL=(root) NOPASSWD: /usr/sbin/nginx -t, /usr/bin/systemctl reload nginx, /usr/bin/tee /etc/nginx/conf.d/chiropro-active.conf
EOF
sudo chmod 440 /etc/sudoers.d/chiropro
sudo visudo -c   # validate

# 4. Add the CI public key so GitHub Actions can SSH in as deploychiro
sudo -u deploychiro mkdir -p /home/deploychiro/.ssh
sudo -u deploychiro tee -a /home/deploychiro/.ssh/authorized_keys < your_ci_key.pub
sudo -u deploychiro chmod 700 /home/deploychiro/.ssh
sudo -u deploychiro chmod 600 /home/deploychiro/.ssh/authorized_keys
```
Then set the GitHub secret `VPS_USER=deploychiro` (and `VPS_SSH_KEY` = the matching
private key). `init-vps.sh` still runs once as **root** (it installs packages and
writes `/etc/nginx`); everyday deploys run as `deploychiro`. `switch.sh` auto-detects
non-root and prefixes the Nginx commands with `sudo`.

> Adjust the paths in the sudoers file if `which` reports different locations
> (e.g. `/bin/systemctl`). The rule is intentionally narrow: `deploychiro` can run
> `nginx -t`, reload nginx, and write ONLY the switch file — nothing else as root.

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
