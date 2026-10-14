# Offcloud on the Pi

Offcloud cloud downloads reach Plex as a read-only WebDAV mount, so three pieces sit beside the
debrid service itself:

| file | what it does |
|---|---|
| `docker-compose.yml` | rclone mounts `https://offcloud.com/webdav` read-only at `/mnt/offcloud` |
| `offcloud_sort.py` + `.service` | links each transfer into `/mnt/offcloud-media/{movies,shows}` and asks Plex to scan |
| `offcloud_repair.py` + `.service`/`.timer` | hourly: re-submits the magnet of any transfer that has gone or stopped serving |

## Repair

`/api/cloud/history` keeps the `originalLink` of every transfer, so a transfer that disappears
from the share can be asked for again — the same idea as zurg's repair pass for Real-Debrid.

A transfer counts as broken when its status is not `downloaded`, when its folder is no longer on
the share, or when the largest file in it will not give up its first and last byte. Before
re-submitting, the magnet must still be cached on Offcloud and its file list must hold a real
video of 50 MB+ with no programs or archives — the same rule `check()` uses in
`debrid/services/offcloud.py`. The relink and the Plex scan are left to the sorter.

What must never be repaired is a release that was removed on purpose, so a hash in
`wrong_releases.json` is skipped for good, `repair-ignore.txt` takes names or hashes by hand, and
a hash is tried at most 3 times, 24 hours apart (`repair-state.json`). Measured on 14 Oct 2026:
`GET /api/cloud/remove/{requestId}` drops the history entry as well, so a deliberate deletion
cannot come back by itself anyway.

Byte-probing costs a ranged read per transfer, so each is probed at most once a day, 25 per pass,
least recently probed first. A pass over 87 transfers takes about 75 seconds.

Cloudflare answers the default `Python-urllib` user agent with an Error 1010 before Offcloud sees
the request, which is why the client sets its own agent.

    python3 /home/pi/offcloud-mount/offcloud_repair.py --once --dry-run   # report only
    journalctl -u offcloud-repair -n 50 --no-pager

## The mount needs its own DNS

The container got SERVFAIL from Docker's embedded resolver for `offcloud.com` for about seven
hours on both 10 and 12 Oct 2026 (`lookup offcloud.com on 127.0.0.11:53: server misbehaving`),
which wedges the mount with EIO and makes the whole library look gone while every link behind it
is alive. `docker-compose.yml` therefore sets `dns:` explicitly. `offcloud_repair.py` also
restarts the container, at most every 15 minutes, when the share lists nothing next to a full
history.

## Install

    sudo install -m 644 offcloud-repair.service offcloud-repair.timer /etc/systemd/system/
    sudo systemctl daemon-reload && sudo systemctl enable --now offcloud-repair.timer

## State of play, 14 Oct 2026

- 87 transfers, 463 files, 297 GB. **Every one serves bytes** — first and last byte read from the
  largest file of all 87. Nothing was rotting when this was written.
- 7 transfers have disappeared since 8 Oct, and all 7 look deliberate: an XXX mis-grab, two
  superseded *Wheel of Time* releases, two wrong *Runner* releases, one *Crow Girl*, and the
  `Heart of the Beast …MeGusta.exe` fake that `verify.py` removed on 13 Oct (it is the single
  hash in `wrong_releases.json`). That is why repair consults that file first.
- The oldest transfer is 6 days old, so Offcloud's retention has not been tested yet. Until it
  is, the repair log is the measurement: `gone from the share` with `offcloud no longer has it
  cached` is retention taking it with a cold magnet; `serves no bytes` is genuine rot, which gets
  re-added.
