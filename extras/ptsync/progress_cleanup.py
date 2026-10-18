#!/usr/bin/env python3
"""Clear stale play progress in Plex that Trakt no longer has.

PlexTraktSync copies progress from Trakt to Plex, but never removes it from Plex once Trakt
drops it (episode finished elsewhere, progress removed on Trakt). This clears it:
for each Plex movie/episode with progress and no matching Trakt playback entry,
- watched items: progress cleared, watched mark kept (re-marked watched)
- unwatched items: reset to not started (leaves Continue Watching)
Items played in Plex in the last SKIP_RECENT_HOURS are left alone. Trakt is only read.

Runs as a dry run (only logs) unless started with --apply.
"""
import json
import sys
import time
import urllib.parse
import urllib.request

PLEX_DEBRID_SETTINGS = '/home/pi/PD2/plex_debrid/settings.json'
TRAKT_TOKEN = '/home/pi/ptsync/config/.pytrakt.json'
SKIP_RECENT_HOURS = 2
APPLY = '--apply' in sys.argv


def log(msg):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), ('' if APPLY else '[dry run] ') + msg, flush=True)


def get_json(url, headers, method='GET'):
    headers = dict(headers, **{'Accept': 'application/json', 'User-Agent': 'progress-cleanup/1.0'})
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers, method=method), timeout=120) as r:
        body = r.read()
    return json.loads(body) if body.strip() else {}


def ids(d):
    return {'%s://%s' % (k, v) for k, v in (d or {}).items() if k in ('imdb', 'tmdb', 'tvdb') and v}


def main():
    settings = json.load(open(PLEX_DEBRID_SETTINGS))
    plex, token = settings['Plex server address'].rstrip('/'), settings['Plex users'][0][1]
    px = {'X-Plex-Token': token}
    t = json.load(open(TRAKT_TOKEN))
    th = {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': t['CLIENT_ID'],
          'Authorization': 'Bearer ' + t['OAUTH_TOKEN']}

    movies, episodes, shows = set(), set(), set()  # trakt playback: movie guids, episode guids, (show guid, s, e)
    for p in get_json('https://api.trakt.tv/sync/playback', th):
        if p.get('type') == 'movie':
            movies |= ids(p['movie'].get('ids'))
        elif p.get('type') == 'episode':
            episodes |= ids(p['episode'].get('ids'))
            for g in ids(p['show'].get('ids')):
                shows.add((g, p['episode']['season'], p['episode']['number']))
    log('Trakt playback: %d movie ids, %d episode ids' % (len(movies), len(episodes)))

    show_guids = {}
    sections = get_json(plex + '/library/sections', px)['MediaContainer'].get('Directory', [])
    candidates = []
    for s in sections:
        if s['type'] not in ('movie', 'show'):
            continue
        kind = '1' if s['type'] == 'movie' else '4'
        items = get_json(plex + '/library/sections/%s/all?type=%s&includeGuids=1' % (s['key'], kind), px)['MediaContainer'].get('Metadata', [])
        candidates += [i for i in items if i.get('viewOffset')]

    now, checked, cleared = time.time(), 0, 0
    for i in candidates:
        checked += 1
        guids = {g['id'] for g in i.get('Guid', [])}
        if i['type'] == 'movie':
            label = '%s (%s)' % (i.get('title'), i.get('year'))
            on_trakt = bool(guids & movies)
        else:
            label = '%s S%02dE%02d' % (i.get('grandparentTitle'), i.get('parentIndex', 0), i.get('index', 0))
            gk = i.get('grandparentRatingKey')
            if gk not in show_guids:
                meta = get_json(plex + '/library/metadata/%s?includeGuids=1' % gk, px)['MediaContainer']['Metadata'][0]
                show_guids[gk] = {g['id'] for g in meta.get('Guid', [])}
            on_trakt = bool(guids & episodes) or any((g, i.get('parentIndex'), i.get('index')) in shows for g in show_guids[gk])
        pos = time.strftime('%H:%M:%S', time.gmtime(i['viewOffset'] / 1000))
        if on_trakt:
            continue
        if now - (i.get('lastViewedAt') or 0) < SKIP_RECENT_HOURS * 3600:
            log('skipped, played in Plex recently: %s at %s' % (label, pos))
            continue
        watched = bool(i.get('viewCount'))
        action = 'clear progress, keep watched' if watched else 'reset to not started'
        if APPLY:
            q = urllib.parse.urlencode({'key': i['ratingKey'], 'identifier': 'com.plexapp.plugins.library'})
            get_json(plex + ('/:/scrobble?' if watched else '/:/unscrobble?') + q, px)
            log('%s: %s (was at %s)' % (action, label, pos))
        else:
            log('would %s: %s (at %s)' % (action, label, pos))
        cleared += 1
    log('%d of %d Plex items with progress %s' % (cleared, checked, 'cleared' if APPLY else 'would be cleared'))


if __name__ == '__main__':
    main()
