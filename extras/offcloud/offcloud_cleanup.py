#!/usr/bin/env python3
"""Delete watched TV episodes from Offcloud 48 hours after they were watched in Plex.

Episodes only, never movies, and only Offcloud copies (files under /mnt/offcloud-media/shows).
A transfer is deleted when every episode in it is marked watched in Plex (also when the mark came
from synced trakt history) and its last watch is older than MIN_AGE_HOURS. After the delete,
offcloud_sort.py drops the link and asks Plex to rescan, and Plex's "empty trash after scan"
removes the episode. Names in keep.txt (one substring per line) are never deleted.

Runs as a dry run (only logs what it would delete) unless started with --apply.
"""
import json
import os
import sys
import time
import urllib.request

MEDIA_SHOWS = '/mnt/offcloud-media/shows/'
PLEX_DEBRID_SETTINGS = '/home/pi/PD2/plex_debrid/settings.json'
CREDENTIALS = os.path.expanduser('~/.config/offcloud/credentials')
KEEP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'keep.txt')
TV_SECTION = '7'
MIN_AGE_HOURS = 48
APPLY = '--apply' in sys.argv


def log(msg):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), ('' if APPLY else '[dry run] ') + msg, flush=True)


def get_json(url, headers):
    headers = dict(headers, **{'Accept': 'application/json', 'User-Agent': 'offcloud-cleanup/1.0'})
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120))


def main():
    settings = json.load(open(PLEX_DEBRID_SETTINGS))
    plex, token = settings['Plex server address'].rstrip('/'), settings['Plex users'][0][1]
    key = dict(l.strip().split('=', 1) for l in open(CREDENTIALS) if '=' in l)['OFFCLOUD_API_KEY']
    oc = {'Authorization': 'Bearer ' + key}
    keep = [l.strip().lower() for l in open(KEEP_FILE)] if os.path.exists(KEEP_FILE) else []
    keep = [k for k in keep if k and not k.startswith('#')]

    episodes = get_json(plex + '/library/sections/' + TV_SECTION + '/all?type=4', {'X-Plex-Token': token})['MediaContainer'].get('Metadata', [])
    now = time.time()
    transfers = {}  # transfer folder -> list of (eligible, label)
    for ep in episodes:
        label = '%s S%02dE%02d' % (ep.get('grandparentTitle'), ep.get('parentIndex', 0), ep.get('index', 0))
        watched = bool(ep.get('viewCount'))
        old_enough = now - (ep.get('lastViewedAt') or now) > MIN_AGE_HOURS * 3600
        for media in ep.get('Media', []):
            for part in media.get('Part', []):
                f = part.get('file', '')
                if f.startswith(MEDIA_SHOWS):
                    transfers.setdefault(f[len(MEDIA_SHOWS):].split('/')[0], []).append((watched and old_enough, label))

    due = [t for t, eps in transfers.items() if all(ok for ok, label in eps) and not any(k in t.lower() for k in keep)]
    if not due:
        log('nothing to delete (%d Offcloud episode transfers checked)' % len(transfers))
        return
    history = {x.get('fileName'): x.get('requestId') for x in get_json('https://offcloud.com/api/cloud/history', oc)}
    for t in sorted(due):
        labels = ', '.join(sorted({label for ok, label in transfers[t]}))
        request_id = history.get(t)
        if request_id is None:
            log('not in Offcloud history, skipped: ' + t)
            continue
        if APPLY:
            result = get_json('https://offcloud.com/api/cloud/remove/' + request_id, oc)
            log(('deleted' if result.get('success') else 'delete FAILED for') + ': ' + labels + ' (' + t + ')')
        else:
            log('would delete: ' + labels + ' (' + t + ')')
    log('%d of %d Offcloud episode transfers %s' % (len(due), len(transfers), 'deleted' if APPLY else 'would be deleted'))


if __name__ == '__main__':
    main()
