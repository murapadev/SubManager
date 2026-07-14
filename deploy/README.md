# SubManager — server deployment (Proxmox LXC)

Runs SubManager headless as a hardened `systemd` oneshot + timer, one tiny
unprivileged Debian LXC per GitHub account.

## Layout inside each container

| Path | Purpose | Perms |
|------|---------|-------|
| `/opt/submanager` | app + venv (`git` clone of the fork) | `submanager:submanager` |
| `/etc/submanager/config.yaml` | per-user YAML config | `640` |
| `/etc/submanager/token.env` | `GITHUB_TOKEN=...` (never in git) | `600` |
| `submanager.service` / `.timer` | oneshot run, twice/day + jitter | — |

The token lives **only** in `token.env` and is injected as an environment
variable; the YAML keeps the `${GITHUB_TOKEN}` placeholder.

## Provision on the Proxmox host (murapa.me)

```bash
# from the repo's deploy/ dir, on the pve host
./provision-lxc.sh 307 submanager-pablo    37 config.pablo.yaml
./provision-lxc.sh 308 submanager-vaishali 38 config.vaishali.yaml
```

Each container: 1 core, 512 MB RAM, 256 MB swap, 2 GB disk, bridge `vmbr1`,
static IP `172.16.1.<octet>`, egress via host NAT.

## After provisioning

1. Put the real token in the container:
   ```bash
   pct exec 307 -- sed -i 's#PUT_YOUR_TOKEN_HERE#ghp_xxx#' /etc/submanager/token.env
   ```
2. For Vaishali, set her GitHub username in `/etc/submanager/config.yaml`.
3. Dry-run first (no changes are made to GitHub):
   ```bash
   pct exec 307 -- sudo -u submanager env GITHUB_TOKEN=ghp_xxx \
     /opt/submanager/.venv/bin/python /opt/submanager/main.py \
     --config /etc/submanager/config.yaml --dry-run
   ```
4. The timer is already enabled; it runs at ~09:00 and ~20:00 with up to 1h
   randomized delay. Logs: `journalctl -u submanager.service`.

## Token scope

A fine-grained PAT with **user → Follow (read/write)** is enough. Nothing else.

## Small-scale tuning

Defaults cap ~15 follows + 15 unfollows per run (~30/day with two runs) and
space individual actions 4–12 s apart. Raise cautiously.

## ⚠️ ToS warning

Automated mass follow/unfollow violates GitHub's Terms of Service and can get
the account rate-limited, flagged, or suspended. Keep volumes low; this is why
the small-scale caps exist. Use at your own risk.
