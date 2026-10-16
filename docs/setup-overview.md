# Setup overview: plex_debrid on the Raspberry Pi

State as of 16 Oct 2026. Branch `offcloud` of `sasagr/plex_debrid`.

## How a new title gets into Plex

```
Plex/Trakt watchlist ─▶ plex_debrid ─▶ Jackett (+FlareSolverr) search
                            │
                            ├─▶ Offcloud (first choice) ─▶ rclone-offcloud mount ─▶ offcloud-sort ─┐
                            └─▶ Real-Debrid (fallback)  ─▶ zurg ─▶ rclone mount ──────────────────┤
                                                                                                    ▼
                                                                                   Plex (instant scan)
```

## 1. systemd services on the Pi

| Service | What it does | How it runs | Log |
|---|---|---|---|
| **plex_debrid** (`plex_debrid.service`) | Watches the Plex/Trakt watchlist, searches, adds to **Offcloud first, Real-Debrid second**. Runs from `/home/pi/PD2/plex_debrid`, branch `offcloud`. | Always on, starts at boot, restarts on failure | `journalctl -u plex_debrid` |
| **Plex Media Server** (`plexmediaserver`) | The library: Movies and TV Shows, each reading both mounts. | Always on, starts at boot | Plex log |
| **offcloud-sort** | Links each Offcloud transfer into `/mnt/offcloud-media/movies` or `/shows` (every 5 s); movies get only their main video and a folder name without site prefixes; removes links of transfers that are gone; triggers Plex scans both ways. | Always on, starts at boot | `journalctl -u offcloud-sort` |
| **offcloud-cleanup** (timer) | Hourly: deletes Offcloud **episodes** that are watched (any watched mark) and whose last watch is more than 48 h ago. Never movies; names in `keep.txt` are protected. | Timer, starts at boot | `journalctl -u offcloud-cleanup` |
| **offcloud-repair** (timer) | Hourly: re-submits Offcloud transfers that vanished or stopped serving (only cached, video-only, never ones in `wrong_releases.json`); restarts the Offcloud mount if it lists nothing. | Timer, starts at boot | `journalctl -u offcloud-repair` |

## 2. Docker containers (all `restart: unless-stopped`)

| Container | Compose folder | What it does |
|---|---|---|
| **zurg** + **rclone** | `/home/pi/zurg-testing` | Real-Debrid as a folder at `/mnt/zurg`; zurg checks Real-Debrid every 5 s and triggers a Plex scan through `scripts/plex_update.sh`. |
| **rclone-offcloud** | `/home/pi/offcloud-mount` | Offcloud WebDAV as a read-only folder at `/mnt/offcloud` (5 s listing cache, 10 GB read cache). |
| **jackett** + **flaresolverr** | `/home/pi/jackett` | Search: TPB, EZTV, YTS, showRSS, Knaben, LimeTorrents, TheRARBG; FlareSolverr gets EZTV past Cloudflare. |
| **plextraktsync_watch** + **scheduler** | `/home/pi/ptsync` | Watched status Plex ⇄ Trakt: live (watch) plus a scheduled full sync. Start with plain `docker compose up -d` in that folder. |

## 3. Rules plex_debrid applies (local changes on branch `offcloud`)

- **Versions:** "1080p SDR" (movies and episodes, 0.2–10 GB) and "2160 SDR" (movies only). Nothing below 1080p.
- **Real-Debrid:** names RD blocks (`WEB-DL`, `WEB.H264`, ...) are skipped for RD only; refused releases are remembered for 3 days; 5 s between adds; releases already in the account count as cached.
- **Offcloud:** real cache check; only cached releases with a real video of 50 MB+ and no programs or archives are added (blocks fake `.exe` releases).
- **Matching:** releases must start with the title (or name the right year); wrong years, `XXX` releases and episodes posing as movies are rejected.
- **Early movies:** a movie may download before its trakt digital date when 2+ proper releases exist, unless another popular film has the same name and year.
- **Checks after each automatic download** (manual picks are never touched): the Plex id must match the watchlist item, the resolution must match the version, and there must be a real video file. Otherwise the release is removed, remembered as wrong for that item and searched again.
- **Missing versions** are fetched based on what Plex actually has (Plex's "2k" counts as 1080p).
- **Safety net:** a failing item is logged and skipped instead of stopping the automation (no more crash loops).

## 4. Settings and secrets (none in Git)

| What | Where |
|---|---|
| plex_debrid settings incl. Plex / Real-Debrid / Offcloud / Jackett keys | `/home/pi/PD2/plex_debrid/settings.json` |
| Trakt app id/secret | `/home/pi/PD2/plex_debrid/.env` |
| Offcloud email + key | `~/.config/offcloud/credentials`, and `rclone.conf` in `/home/pi/offcloud-mount` |
| plex_debrid's working files | `downloads.jsonl`, `wrong_releases.json`, `rd_refused.json` in the plex_debrid folder |
| Offcloud scripts and units (copies) | `extras/offcloud/` in this repository |

## 5. Plex, network and Pi settings that matter

- **Plex scanning:** library scan every 30 min plus instant targeted scans; "Empty trash automatically after every scan" on; nightly "Update all libraries during maintenance" off.
- **Remote access:** direct connection through a manual port forward on the modem to the Pi's port 32400. Control D bypasses `servers.plex.tv`, `v4.plex.tv` and `*.plex.direct`; Plex Discover stays on the Control D US redirect.
- **Pi DNS:** stays on Tailscale DNS. Pointing it at `ctrld` (127.0.0.1) breaks DNS inside all Docker containers.
- **Disabled on purpose:** Jellyfin (memory). Fan switches on at 65 °C.

## Handy commands

```bash
systemctl status plex_debrid offcloud-sort
systemctl list-timers | grep offcloud
journalctl -u plex_debrid -f
sudo systemctl restart plex_debrid
```
