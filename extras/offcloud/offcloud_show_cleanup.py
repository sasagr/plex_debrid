#!/usr/bin/env python3
"""Delete a TV show's Offcloud episodes once the show is dropped on Trakt or removed from the Plex watchlist.

TV shows only, never movies, and only Offcloud copies (files under /mnt/offcloud-media/shows).
A show in the Plex TV library is "gone" when
  * it is on the trakt "dropped" list, or
  * it was seen on the Plex watchlist by an earlier run and is no longer on it.
Shows never seen on the watchlist and not dropped (e.g. added by hand) are left alone, and so is a
show still on the trakt watchlist, since plex_debrid watches that list too and would fetch it again.
A gone show's transfers are deleted after GRACE_HOURS; putting it back on the watchlist (or
un-dropping it) before then cancels the delete. After the delete offcloud_sort.py drops the links
and asks Plex to rescan, and Plex's "empty trash after scan" removes the episodes.
Names in keep.txt (one substring per line, show title or transfer name) are never deleted.

Runs as a dry run (only logs what it would delete) unless started with --apply.
"""
import json
import os
import sys
import time
import urllib.request

MEDIA_SHOWS = '/mnt/offcloud-media/shows/'
PLEX_DEBRID_SETTINGS = '/home/pi/PD2/plex_debrid/settings.json'
TRAKT_TOKEN = '/home/pi/ptsync/config/.pytrakt.json'
CREDENTIALS = os.path.expanduser('~/.config/offcloud/credentials')
HERE = os.path.dirname(os.path.abspath(__file__))
KEEP_FILE = os.path.join(HERE, 'keep.txt')
STATE = os.path.join(HERE, 'show-cleanup-state.json')
TV_SECTION = '7'
GRACE_HOURS = 24
APPLY = '--apply' in sys.argv


def log(msg):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), ('' if APPLY else '[dry run] ') + msg, flush=True)


def get_json(url, headers):
    headers = dict(headers, **{'Accept': 'application/json', 'User-Agent': 'offcloud-show-cleanup/1.0'})
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120))


def ids(d):
    return {'%s://%s' % (k, v) for k, v in (d or {}).items() if k in ('imdb', 'tmdb', 'tvdb') and v}


def main():
    settings = json.load(open(PLEX_DEBRID_SETTINGS))
    plex, token = settings['Plex server address'].rstrip('/'), settings['Plex users'][0][1]
    px = {'X-Plex-Token': token}
    t = json.load(open(TRAKT_TOKEN))
    th = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': t['CLIENT_ID'],
          'Authorization': 'Bearer ' + t['OAUTH_TOKEN']}
    key = dict(l.strip().split('=', 1) for l in open(CREDENTIALS) if '=' in l)['OFFCLOUD_API_KEY']
    oc = {'Authorization': 'Bearer ' + key}
    keep = [l.strip().lower() for l in open(KEEP_FILE)] if os.path.exists(KEEP_FILE) else []
    keep = [k for k in keep if k and not k.startswith('#')]
    state = json.load(open(STATE)) if os.path.exists(STATE) else {'seen': {}, 'gone_since': {}}

    # any list that cannot be read in full makes the whole run unsafe: deleting needs all three
    watchlist, start = [], 0
    while True:
        page = get_json('https://discover.provider.plex.tv/library/sections/watchlist/all?X-Plex-Container-Size=100'
                        '&X-Plex-Container-Start=%d' % start, px)['MediaContainer']
        watchlist += page.get('Metadata', [])
        start += 100
        if start >= page.get('totalSize', 0):
            break
    if not watchlist:
        log('the Plex watchlist came back empty, not trusting it this run')
        return
    on_watchlist = {m['guid'] for m in watchlist if m.get('type') == 'show'}
    dropped = {}
    for d in get_json('https://api.trakt.tv/users/hidden/dropped?type=show&limit=1000', th):
        for g in ids(d['show'].get('ids')):
            dropped[g] = d['show']['title']
    trakt_watchlist = set()
    for w in get_json('https://api.trakt.tv/sync/watchlist/shows', th):
        trakt_watchlist |= ids(w['show'].get('ids'))

    shows = get_json(plex + '/library/sections/' + TV_SECTION + '/all?includeGuids=1', px)['MediaContainer'].get('Metadata', [])
    episodes = get_json(plex + '/library/sections/' + TV_SECTION + '/all?type=4', px)['MediaContainer'].get('Metadata', [])
    transfers = {}  # show ratingKey -> transfer folders holding its episodes
    owners = {}     # transfer folder -> show ratingKeys with episodes in it
    for ep in episodes:
        for media in ep.get('Media', []):
            for part in media.get('Part', []):
                f = part.get('file', '')
                if f.startswith(MEDIA_SHOWS):
                    folder = f[len(MEDIA_SHOWS):].split('/')[0]
                    transfers.setdefault(ep['grandparentRatingKey'], set()).add(folder)
                    owners.setdefault(folder, set()).add(ep['grandparentRatingKey'])

    now = time.time()
    seen, gone_since = state['seen'], state['gone_since']
    due = []
    for show in shows:
        guid, title = show.get('guid', ''), show['title']
        if guid in on_watchlist:
            seen[guid] = title
        if show['ratingKey'] not in transfers:
            continue
        external = {g['id'] for g in show.get('Guid', [])}
        is_dropped = bool(external & set(dropped))
        removed = guid in seen and guid not in on_watchlist
        if not (is_dropped or removed):
            gone_since.pop(guid, None)
            continue
        reason = 'dropped on trakt' if is_dropped else 'removed from the Plex watchlist'
        if guid in on_watchlist:
            log('%s is %s but still on the Plex watchlist, skipped' % (title, reason))
            gone_since.pop(guid, None)
            continue
        if external & trakt_watchlist:
            log('%s is %s but still on the trakt watchlist, skipped' % (title, reason))
            gone_since.pop(guid, None)
            continue
        if any(k in title.lower() for k in keep):
            log('%s is %s but protected by keep.txt' % (title, reason))
            continue
        since = gone_since.setdefault(guid, now)
        left = since + GRACE_HOURS * 3600 - now
        if left > 0:
            log('%s is %s: %d Offcloud transfers will be deleted in %.1f h' % (title, reason, len(transfers[show['ratingKey']]), left / 3600))
            if not APPLY:
                for folder in sorted(transfers[show['ratingKey']]):
                    log('    ' + folder)
            continue
        due.append((title, reason, show['ratingKey']))

    if due:
        history = {x.get('fileName'): x.get('requestId') for x in get_json('https://offcloud.com/api/cloud/history', oc)}
        for title, reason, rk in due:
            for folder in sorted(transfers[rk]):
                if owners.get(folder, set()) != {rk}:
                    log('skipped %s: it also holds episodes of another show' % folder)
                    continue
                if any(k in folder.lower() for k in keep):
                    log('skipped %s: protected by keep.txt' % folder)
                    continue
                request_id = history.get(folder)
                if request_id is None:
                    log('not in Offcloud history, skipped: ' + folder)
                    continue
                if APPLY:
                    result = get_json('https://offcloud.com/api/cloud/remove/' + request_id, oc)
                    log(('deleted' if result.get('success') else 'delete FAILED for') + ': %s (%s) %s' % (title, reason, folder))
                else:
                    log('would delete: %s (%s) %s' % (title, reason, folder))
    if APPLY:
        tmp = STATE + '.tmp'
        json.dump(state, open(tmp, 'w'), indent=1, sort_keys=True)
        os.replace(tmp, STATE)
    log('%d shows on the Plex watchlist, %d dropped on trakt, %d shows with Offcloud episodes, %d due now'
        % (len(on_watchlist), len(set(dropped.values())), len(transfers), len(due)))


if __name__ == '__main__':
    main()
