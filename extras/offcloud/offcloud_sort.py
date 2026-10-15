#!/usr/bin/env python3
"""Keep /mnt/offcloud-media/{movies,shows} in sync with the Offcloud WebDAV mount.

Offcloud lists one folder per transfer with no movie/show split, so this links each transfer
folder into movies/ or shows/ (by its name) for the Plex libraries, asks Plex to scan new or
removed folders, and removes links whose transfer Offcloud deleted (after 30 days).
Movies only get their biggest video file (like zurg's only_show_the_biggest_file), so extras and
samples in the torrent don't show up in Plex as extra versions; shows keep the whole folder.
"""
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request

MOUNT = '/mnt/offcloud'
MEDIA = '/mnt/offcloud-media'
PLEX_SECTIONS = {'movies': '6', 'shows': '7'}
PLEX_DEBRID_SETTINGS = '/home/pi/PD2/plex_debrid/settings.json'
INTERVAL = 5
VIDEO = ('.mkv', '.mp4', '.avi', '.m4v', '.mov', '.ts', '.m2ts', '.wmv', '.mpg', '.mpeg', '.webm')

SHOW = re.compile(r'(?<![a-z0-9])(s\d{1,2}[ ._-]?e\d{1,3}|s\d{1,2}(?![0-9])|season[ ._-]?\d{1,2}|\d{1,2}x\d{2})(?![0-9])', re.I)


def log(msg):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), msg, flush=True)


# site prefixes like 'www.UIndex.org - ' confuse Plex's movie matching ("Www UIndex Org Sleuth")
SITE_PREFIX = re.compile(r'^(?:www\.[a-z0-9.-]+\s*-\s*|\[[^\]]*\]\s*)+', re.I)


def clean_name(name):
    return SITE_PREFIX.sub('', name).strip() or name


def category(name):
    return 'shows' if SHOW.search(name) else 'movies'


def biggest_video(transfer):
    # path of the biggest video inside a transfer folder, relative to it (None if none is listed yet)
    best = None
    for root, dirs, files in os.walk(transfer):
        for f in files:
            if f.lower().endswith(VIDEO):
                size = os.path.getsize(os.path.join(root, f))
                if best is None or size > best[0]:
                    best = (size, os.path.relpath(os.path.join(root, f), transfer))
    return best[1] if best else None


def link_movie(name, link_name):
    # movies/<link_name>/<same relative path> -> the biggest video only; returns False if not ready yet
    source = os.path.join(MOUNT, name)
    target = os.path.join(MEDIA, 'movies', link_name)
    if os.path.isfile(source):
        os.symlink(source, target)
        return True
    video = biggest_video(source)
    if video is None:
        return False
    os.makedirs(os.path.dirname(os.path.join(target, video)), exist_ok=True)
    os.symlink(os.path.join(source, video), os.path.join(target, video))
    return True


def unlink(path):
    # links are removed without following them; movie folders only contain links
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    else:
        shutil.rmtree(path)


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
    # link name for every transfer: movies without site prefixes, shows as they are
    wanted = {}
    for name in sorted(transfers):
        link_name = clean_name(name) if category(name) == 'movies' else name
        wanted[link_name if link_name not in wanted else name] = name
    for link_name in sorted(set(wanted) - set(links)):
        name = wanted[link_name]
        cat = category(name)
        link = os.path.join(MEDIA, cat, link_name)
        if cat == 'movies':
            if not link_movie(name, link_name):
                continue  # not listed completely yet, next round
        else:
            os.symlink(os.path.join(MOUNT, name), link)
        log('linked ' + cat + ': ' + link_name)
        plex_scan(PLEX_SECTIONS[cat], link)
    # movies linked the old way (whole folder) are switched to the biggest video only
    for link_name in sorted(n for n, c in links.items() if c == 'movies' and n in wanted):
        link = os.path.join(MEDIA, 'movies', link_name)
        if os.path.islink(link) and os.path.isdir(link):
            os.remove(link)
            if link_movie(wanted[link_name], link_name):
                log('movie reduced to its main video: ' + link_name)
                plex_scan(PLEX_SECTIONS['movies'], link)
            else:
                os.symlink(os.path.join(MOUNT, wanted[link_name]), link)
    for link_name in sorted(set(links) - set(wanted)):
        cat = links[link_name]
        unlink(os.path.join(MEDIA, cat, link_name))
        log('removed ' + cat + ': ' + link_name)
        plex_scan(PLEX_SECTIONS[cat], os.path.join(MEDIA, cat))


if __name__ == '__main__':
    log('offcloud sorter started')
    while True:
        try:
            sync()
        except Exception as e:
            log('sync error: ' + str(e))
        time.sleep(INTERVAL)
