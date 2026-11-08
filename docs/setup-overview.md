# Setup overview: plex_debrid on the Raspberry Pi

State as of 8 Nov 2026. Branch `offcloud` of `sasagr/plex_debrid`.

## How a new title gets into Plex

```
Plex/Trakt watchlist ─▶ plex_debrid ─▶ Jackett (+FlareSolverr) search
                            │
                            └─▶ Offcloud ─▶ rclone-offcloud mount ─▶ offcloud-sort ─┐
                                                                                     ▼
                                                                    Plex (instant scan)
```

## 1. systemd services on the Pi

| Service | What it does | How it runs | Log |
|---|---|---|---|
| **plex_debrid** (`plex_debrid.service`) | Watches the Plex/Trakt watchlist, searches, adds to **Offcloud** (Real-Debrid removed 8 Nov 2026, premium expired). Runs from `/home/pi/PD2/plex_debrid`, branch `offcloud`. | Always on, starts at boot, restarts on failure | `journalctl -u plex_debrid` |
| **Plex Media Server** (`plexmediaserver`) | The library: Movies and TV Shows, each reading only `/mnt/offcloud-media` (the `/mnt/zurg` folders were removed 8 Nov 2026). | Always on, starts at boot | Plex log |
| **offcloud-sort** | Links each Offcloud transfer into `/mnt/offcloud-media/movies` or `/shows` (every 5 s); movies get only their main video and a folder name without site prefixes; removes links of transfers that are gone; triggers Plex scans both ways. | Always on, starts at boot | `journalctl -u offcloud-sort` |
| **offcloud-cleanup** (timer) | Hourly: deletes Offcloud **episodes** that are watched (any watched mark) and whose last watch is more than 48 h ago. Never movies; names in `keep.txt` are protected. | Timer, starts at boot | `journalctl -u offcloud-cleanup` |
| **offcloud-show-cleanup** (timer) | Hourly: deletes **all** Offcloud episodes of a TV show that is dropped on Trakt or was removed from the Plex watchlist, 24 h after it happened (putting it back cancels). Never movies; skips shows still on either watchlist, shows never seen on the watchlist (manual additions) and `keep.txt` names. | Timer, starts at boot | `journalctl -u offcloud-show-cleanup` |
| **ptsync-progress-cleanup** (timer) | Every 30 min (:05 and :35, after the PlexTraktSync sync): clears Plex play progress that Trakt no longer has, since PlexTraktSync only ever adds progress. Watched items keep their watched mark; unwatched ones go back to not started. Skips anything played in Plex in the last 2 h; never writes to Trakt. Script in `/home/pi/ptsync`. | Timer, starts at boot | `journalctl -u ptsync-progress-cleanup` |
| **wifi-watchdog** (timer) | Every 2 min: if the Wi-Fi is not connected and not connecting, reconnects to the home network (NetworkManager gives up for good after the router refuses it for a while, 22 Oct 2026 outage); if connected on a weak signal (below −70 dBm), moves to a much stronger access point of the same network (the Pi got stuck on a weak extender on 1 Nov 2026). Script in `/home/pi/netwatch`. | Timer, starts at boot | `journalctl -u wifi-watchdog` |
| **offcloud-repair** (timer) | Hourly: re-submits Offcloud transfers that vanished or stopped serving (only cached, video-only, never ones in `wrong_releases.json`); restarts the Offcloud mount if it lists nothing (not while offcloud.com is unreachable), starts the mount container if it is stopped, and clears a stale mount that blocks the start. | Timer, starts at boot | `journalctl -u offcloud-repair` |

## 2. Docker containers (`restart: unless-stopped` unless noted)

| Container | Compose folder | What it does |
|---|---|---|
| **zurg** + **rclone** | `/home/pi/zurg-testing` | **Stopped, restart policy `no`** (Real-Debrid premium expired 8 Nov 2026). Was Real-Debrid as a folder at `/mnt/zurg`. To bring back: `docker update --restart=unless-stopped zurg rclone && docker start zurg rclone`, re-add the Real-Debrid key and service in plex_debrid and the `/mnt/zurg` folders in Plex. |
| **rclone-offcloud** | `/home/pi/offcloud-mount` | Offcloud WebDAV as a read-only folder at `/mnt/offcloud` (5 s listing cache, 10 GB read cache). |
| **jackett** + **flaresolverr** | `/home/pi/jackett` | Search: TPB, EZTV, YTS, showRSS, Knaben, LimeTorrents, TheRARBG; FlareSolverr gets EZTV past Cloudflare. |
| **plextraktsync_watch** + **scheduler** | `/home/pi/ptsync` | Watched status Plex ⇄ Trakt: live (watch) plus a scheduled full sync. Start with plain `docker compose up -d` in that folder. |

## 3. Rules plex_debrid applies (local changes on branch `offcloud`)

- **Versions:** "1080p SDR" (movies and episodes, 0.2–10 GB) and "2160 SDR" (movies only). Nothing below 1080p.
- **Real-Debrid (not in use since 8 Nov 2026):** names RD blocks (`WEB-DL`, `WEB.H264`, ...) are skipped for RD only; refused releases are remembered for 3 days; 5 s between adds; releases already in the account count as cached.
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
| Plex progress cleanup script and units (copies) | `extras/ptsync/` in this repository |
| Wi-Fi watchdog script and units (copies) | `extras/netwatch/` in this repository |

## 5. Plex, network and Pi settings that matter

- **Plex scanning:** library scan every 30 min plus instant targeted scans; "Empty trash automatically after every scan" on; nightly "Update all libraries during maintenance" off.
- **Remote access:** direct connection through a manual port forward on the modem to the Pi's port 32400. Control D bypasses `servers.plex.tv`, `v4.plex.tv` and `*.plex.direct`; Plex Discover stays on the Control D US redirect.
- **Pi DNS:** stays on Tailscale DNS. Pointing it at `ctrld` (127.0.0.1) breaks DNS inside all Docker containers.
- **Disabled on purpose:** Jellyfin (memory); `RD.service`, the old direct Real-Debrid mount at `/mnt/RD` from before zurg; since 8 Nov 2026 (Real-Debrid expired) also `zurg-rclone.service` (second `/mnt/zurg` mount), the zurg containers and the `@reboot rclone serve http RD:/torrents` line (Switch files on port 1111) in the pi crontab, commented out. Fan switches on at 65 °C.

## Handy commands

```bash
systemctl status plex_debrid offcloud-sort
systemctl list-timers | grep offcloud
journalctl -u plex_debrid -f
sudo systemctl restart plex_debrid
```
