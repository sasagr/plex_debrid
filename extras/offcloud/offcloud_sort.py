#!/usr/bin/env python3
"""Keep /mnt/offcloud-media/{movies,shows} in sync with the Offcloud WebDAV mount.

Offcloud lists one folder per transfer with no movie/show split, so this links each transfer
folder into movies/ or shows/ (by its name) for the Plex libraries, asks Plex to scan new or
removed folders, and removes links whose transfer Offcloud deleted (after 30 days).
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request

MOUNT = '/mnt/offcloud'
MEDIA = '/mnt/offcloud-media'
PLEX_SECTIONS = {'movies': '6', 'shows': '7'}
PLEX_DEBRID_SETTINGS = '/home/pi/PD2/plex_debrid/settings.json'
INTERVAL = 15

SHOW = re.compile(r'(?<![a-z0-9])(s\d{1,2}[ ._-]?e\d{1,3}|s\d{1,2}(?![0-9])|season[ ._-]?\d{1,2}|\d{1,2}x\d{2})(?![0-9])', re.I)


def log(msg):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), msg, flush=True)


def category(name):
    return 'shows' if SHOW.search(name) else 'movies'


def plex_scan(section, path):
    try:
        settings = json.load(open(PLEX_DEBRID_SETTINGS))
        base = settings['Plex server address'].rstrip('/')
        token = settings['Plex users'][0][1]
        query = urllib.parse.urlencode({'path': path, 'X-Plex-Token': token})
        urllib.request.urlopen(urllib.request.Request(base + '/library/sections/' + section + '/refresh?' + query, method='GET'), timeout=20)
    except Exception as e:
        log('plex scan failed for ' + path + ': ' + str(e))


def sync():
    if not os.path.ismount(MOUNT):
        log('offcloud mount is not available, skipping')
        return
    try:
        transfers = set(os.listdir(MOUNT))
    except OSError as e:
        log('cannot list offcloud mount: ' + str(e))
        return
    links = {}
    for cat in PLEX_SECTIONS:
        os.makedirs(os.path.join(MEDIA, cat), exist_ok=True)
        for name in os.listdir(os.path.join(MEDIA, cat)):
            links[name] = cat
    # an empty listing while links exist is more likely a mount hiccup than 30-day expiry of everything
    if len(transfers) == 0 and len(links) > 0:
        log('offcloud mount lists no transfers, keeping existing links')
        return
    for name in sorted(transfers - set(links)):
        cat = category(name)
        link = os.path.join(MEDIA, cat, name)
        os.symlink(os.path.join(MOUNT, name), link)
        log('linked ' + cat + ': ' + name)
        plex_scan(PLEX_SECTIONS[cat], link)
    for name in sorted(set(links) - transfers):
        cat = links[name]
        os.remove(os.path.join(MEDIA, cat, name))
        log('removed expired ' + cat + ': ' + name)
        plex_scan(PLEX_SECTIONS[cat], os.path.join(MEDIA, cat))


if __name__ == '__main__':
    log('offcloud sorter started')
    while True:
        try:
            sync()
        except Exception as e:
            log('sync error: ' + str(e))
        time.sleep(INTERVAL)
