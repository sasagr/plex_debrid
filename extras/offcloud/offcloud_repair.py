#!/usr/bin/env python3
"""Repair the Offcloud library the way zurg repairs Real-Debrid.

Offcloud keeps the magnet it downloaded from in /api/cloud/history, so a transfer that has
gone from the WebDAV share can be asked for again. Each pass:

  * lists the cloud history and the WebDAV share, and calls a transfer broken when it failed,
    when its folder is no longer on the share, or when its largest file will not serve bytes;
  * re-submits the original magnet for the broken ones, but only if Offcloud still reports it
    cached, it holds a real video file and plex_debrid did not reject it on purpose;
  * leaves the relinking and the Plex scan to offcloud_sort.py, which polls every 5 seconds.

Transfers removed deliberately (a wrong title, a fake ".exe" release) must never come back, so
anything whose hash plex_debrid wrote to wrong_releases.json is skipped for good, and every
repaired hash is remembered to stop an unfixable one being re-added for ever.

  offcloud_repair.py --once [--dry-run]    one pass; --dry-run only reports
  offcloud_repair.py                       a pass every hour
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

MOUNT = '/mnt/offcloud'
RCLONE_CONF = '/home/pi/offcloud-mount/rclone.conf'
PD = '/home/pi/PD2/plex_debrid'
SETTINGS = os.path.join(PD, 'settings.json')
WRONG = os.path.join(PD, 'wrong_releases.json')
IGNORE = '/home/pi/offcloud-mount/repair-ignore.txt'
STATE = '/home/pi/offcloud-mount/repair-state.json'
CONTAINER = 'rclone-offcloud'

INTERVAL = 3600          # seconds between passes
DOWNLOAD_WAIT = 120      # seconds to wait for a re-submitted magnet to reach "downloaded"
MAX_ATTEMPTS = 3         # give up on a hash after this many repairs that did not stick
RETRY_AFTER = 86400      # seconds before the same hash may be tried again
PROBE_EVERY = 86400      # seconds before a healthy transfer is byte-probed again
PROBE_BUDGET = 25        # byte-probes per pass, oldest probe first
RESTART_COOLDOWN = 900   # seconds between container restarts when the mount is wedged

# mirrors debrid/services/offcloud.py: a transfer is only worth having if it holds a real video
VIDEO = ('.mkv', '.mp4', '.avi', '.m4v', '.mov', '.ts', '.m2ts', '.mts', '.wmv', '.mpg', '.mpeg',
         '.webm', '.flv', '.vob', '.ogv')
BLOCKED = ('.exe', '.scr', '.bat', '.cmd', '.com', '.msi', '.lnk', '.vbs', '.js', '.jar', '.apk',
           '.ps1', '.zip', '.rar', '.7z')
MIN_VIDEO_SIZE = 50 * 1000 * 1000

BTIH = re.compile(r'xt=urn:btih:([0-9a-zA-Z]+)', re.I)

# Cloudflare answers the default "Python-urllib" agent with an Error 1010 before Offcloud sees it
AGENT = 'offcloud-repair/1.0'


def log(msg):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), msg, flush=True)


def api_key():
    return json.load(open(SETTINGS))['Offcloud API Key']


def api(method, path, payload=None, timeout=60):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request('https://offcloud.com' + path, data=body, method=method,
                                     headers={'Authorization': 'Bearer ' + api_key(),
                                              'Accept': 'application/json',
                                              'Content-Type': 'application/json',
                                              'User-Agent': AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        log('api ' + path + ' failed: ' + str(e.code) + ' ' + e.read()[:200].decode('utf-8', 'replace'))
    except Exception as e:
        log('api ' + path + ' failed: ' + str(e))
    return None


def history():
    response = api('GET', '/api/cloud/history')
    if isinstance(response, dict):
        response = response.get('history', [])
    return response if isinstance(response, list) else []


def hex_hash(value):
    # magnets carry either a 40-character hex hash or a 32-character base32 one
    value = (value or '').strip()
    if len(value) == 40:
        return value.lower()
    try:
        return base64.b16encode(base64.b32decode(value.upper())).decode().lower()
    except Exception:
        return value.lower()


def magnet_hash(link):
    found = BTIH.search(link or '')
    return hex_hash(found.group(1)) if found else ''


def ignored():
    """Names or hashes never to repair, one per line - for anything removed by hand."""
    try:
        return {line.strip().lower() for line in open(IGNORE) if line.strip() and not line.startswith('#')}
    except Exception:
        return set()


def rejected_hashes():
    # hashes plex_debrid removed on purpose: a wrong title or a release with no video in it
    try:
        return {h.lower() for hashes in json.load(open(WRONG)).values() for h in hashes}
    except Exception:
        return set()


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save_state(state):
    tmp = STATE + '.tmp'
    json.dump(state, open(tmp, 'w'), indent=1, sort_keys=True)
    os.replace(tmp, STATE)


def restart_mount(state):
    last = state.get('_mount_restart', 0)
    if time.time() - last < RESTART_COOLDOWN:
        return
    state['_mount_restart'] = time.time()
    log('mount is wedged, restarting ' + CONTAINER)
    subprocess.run(['docker', 'restart', CONTAINER], capture_output=True)


def share_names(state):
    """Folder names on the WebDAV share, or None when the share cannot be trusted this pass."""
    if not os.path.ismount(MOUNT):
        log('mount is not available, skipping pass')
        return None
    try:
        return set(os.listdir(MOUNT))
    except OSError as e:
        log('cannot list the share: ' + str(e))
        restart_mount(state)
        return None


def rclone(args, timeout=120):
    return subprocess.run(['rclone', '--config', RCLONE_CONF] + args,
                           capture_output=True, timeout=timeout)


def serves_bytes(name):
    """True when the transfer's largest file gives up its first and last byte."""
    listing = rclone(['lsjson', 'offcloud:' + name, '--recursive', '--files-only'])
    try:
        files = json.loads(listing.stdout)
    except Exception:
        return False
    if len(files) == 0:
        return False
    biggest = max(files, key=lambda f: f.get('Size', 0))
    path = 'offcloud:' + name + '/' + biggest['Path']
    for end in ('--head', '--tail'):
        try:
            read = rclone(['cat', end, '1', path, '--retries', '1', '--low-level-retries', '1',
                           '--timeout', '25s'])
        except subprocess.TimeoutExpired:
            return False
        if len(read.stdout) != 1:
            return False
    return True


def cached_with_video(magnet):
    """(cached, has a real video and nothing blocked) for a magnet, without adding it."""
    response = api('POST', '/api/cache/info', {'urls': [magnet], 'includeFiles': True})
    if not isinstance(response, list) or len(response) != 1 or not isinstance(response[0], dict):
        return None, None
    info = response[0]
    if not info.get('cached'):
        return False, None
    names = [(str(f.get('filename', '')).lower(), f.get('size', 0) or 0) for f in info.get('files', [])]
    good = (any(n.endswith(VIDEO) and s >= MIN_VIDEO_SIZE for n, s in names)
            and not any(n.endswith(BLOCKED) for n, s in names))
    return True, good


def wait_downloaded(request_id):
    start = time.time()
    while time.time() - start < DOWNLOAD_WAIT:
        for item in history():
            if item.get('requestId') == request_id:
                if item.get('status') in ['downloaded', 'error', 'canceled']:
                    return item.get('status')
        time.sleep(5)
    return 'timeout'


def repair(item, state, dry_run):
    name = item.get('fileName') or item.get('requestId')
    magnet = item.get('originalLink') or ''
    digest = magnet_hash(magnet)
    if not magnet.startswith('magnet:') or not digest:
        log('cannot repair ' + name + ': no magnet in its history entry')
        return
    if digest in rejected_hashes():
        log('not repairing ' + name + ': plex_debrid rejected this release on purpose')
        return
    skip = ignored()
    if digest in skip or name.lower() in skip:
        log('not repairing ' + name + ': it is in repair-ignore.txt')
        return
    seen = state.get(digest, {})
    if seen.get('attempts', 0) >= MAX_ATTEMPTS:
        log('not repairing ' + name + ': gave up after ' + str(seen['attempts']) + ' attempts')
        return
    if time.time() - seen.get('last', 0) < RETRY_AFTER:
        return
    cached, good = cached_with_video(magnet)
    if cached is None:
        return
    if not cached:
        log('cannot repair ' + name + ': offcloud no longer has it cached')
        state[digest] = dict(seen, name=name, last=time.time(), outcome='uncached')
        save_state(state)
        return
    if not good:
        log('not repairing ' + name + ': no real video file, or it holds programs or archives')
        state[digest] = dict(seen, name=name, last=time.time(), outcome='not a video')
        save_state(state)
        return
    if dry_run:
        log('would repair ' + name)
        return
    response = api('POST', '/api/cloud', {'url': magnet})
    if not isinstance(response, dict) or 'requestId' not in response:
        log('repair of ' + name + ' was refused by offcloud')
        return
    status = response.get('status')
    if status != 'downloaded':
        status = wait_downloaded(response['requestId'])
    state[digest] = {'name': response.get('fileName') or name, 'attempts': seen.get('attempts', 0) + 1,
                     'last': time.time(), 'outcome': status}
    save_state(state)
    log(('repaired ' if status == 'downloaded' else 'repair ended in [' + str(status) + '] for ')
        + (response.get('fileName') or name))


def pass_once(dry_run=False):
    state = load_state()
    names = share_names(state)
    if names is None:
        return
    items = [i for i in history() if i.get('fileName')]
    if len(items) == 0:
        log('cloud history is empty or unreadable, skipping pass')
        return
    # an empty share next to a full history is a mount problem, not 87 dead transfers
    if len(names) == 0:
        log('the share lists nothing while the history has ' + str(len(items)) + ' transfers, skipping pass')
        restart_mount(state)
        return

    broken, healthy = [], []
    for item in items:
        if item.get('status') != 'downloaded':
            log('failed transfer: ' + item['fileName'] + ' [' + str(item.get('status')) + ']')
            broken.append(item)
        elif item['fileName'] not in names:
            log('gone from the share: ' + item['fileName'])
            broken.append(item)
        else:
            healthy.append(item)

    # byte-probe the healthy ones in rotation: a link can be listed and still serve nothing
    probes = sorted(healthy, key=lambda i: state.get(magnet_hash(i.get('originalLink')), {}).get('probed', 0))
    for item in probes[:PROBE_BUDGET]:
        digest = magnet_hash(item.get('originalLink'))
        if digest and time.time() - state.get(digest, {}).get('probed', 0) < PROBE_EVERY:
            continue
        if serves_bytes(item['fileName']):
            if digest:
                state[digest] = dict(state.get(digest, {}), name=item['fileName'], probed=time.time())
        else:
            log('serves no bytes: ' + item['fileName'])
            broken.append(item)
    save_state(state)

    log(str(len(items)) + ' transfers, ' + str(len(broken)) + ' broken')
    for item in broken:
        repair(item, state, dry_run)


if __name__ == '__main__':
    dry_run = '--dry-run' in sys.argv
    if '--once' in sys.argv:
        pass_once(dry_run)
    else:
        log('offcloud repair started')
        while True:
            try:
                pass_once(dry_run)
            except Exception as e:
                log('pass error: ' + str(e))
            time.sleep(INTERVAL)
