#import modules
from base import *
from ui.ui_print import *
import threading

# Verify downloads by id: after a release was added to a debrid service, wait for Plex to identify the new
# file and compare its plex:// guid with the guid of the watchlisted item (show level for episodes/seasons).
# A wrong title is removed from the debrid service again and its hash is remembered for that item, so the
# next search skips it.

journal_file = 'downloads.jsonl'
wrong_file = 'wrong_releases.json'
check_every = 30          # seconds between checks
give_up_after = 1800      # seconds: a download that never shows up in Plex is only logged
_lock = threading.Lock()
_thread = None


def expected_guid(element):
    # plex:// guid of what was asked for: the movie or show (episodes/seasons are checked at show level).
    # Items that only carry other ids (e.g. a number from trakt) can't be checked against Plex: None
    if element.type == 'episode':
        guid = getattr(element, 'grandparentGuid', None)
    elif element.type == 'season':
        guid = getattr(element, 'parentGuid', None)
    else:
        guid = getattr(element, 'guid', None)
    return guid if isinstance(guid, str) and guid.startswith('plex://') else None


def hex_hash(info_hash):
    from debrid.services.realdebrid import hex_hash as to_hex
    return to_hex(info_hash or '')


def wrong_hashes(element):
    guid = expected_guid(element)
    if guid is None:
        return set()
    try:
        return set(json.load(open(wrong_file)).get(guid, []))
    except Exception:
        return set()


def required_resolution(element):
    # the resolution the downloaded version asks for ("1080p SDR" -> 1080), None if it has no exact requirement
    for rule in getattr(getattr(element, 'version', None), 'rules', []):
        if rule[0] == 'resolution' and rule[1] == 'requirement' and rule[2] == '==' and str(rule[3]).isdigit():
            return int(rule[3])
    return None


def plex_resolution(value):
    # Plex's videoResolution: '4k', '2k' (wide 1080p), '1080', '720', '480', 'sd'; None/'' when unknown (e.g. 1080i)
    value = str(value or '').lower()
    return {'4k': 2160, '2k': 1080, 'sd': 480}.get(value, int(value) if value.isdigit() else None)


def record(element, release, service):
    # called after a successful download; needs the service's id and folder name of the added torrent
    guid = expected_guid(element)
    if guid is None or not getattr(release, 'debrid_id', None) or not getattr(release, 'debrid_name', None):
        return
    entry = {'time': time.time(), 'service': service, 'id': release.debrid_id, 'name': release.debrid_name,
             'hash': hex_hash(getattr(release, 'hash', '')), 'title': release.title, 'expected': guid,
             'type': element.type, 'ratingKey': guid.split('/')[-1], 'query': element.query(),
             'version': getattr(getattr(element, 'version', None), 'name', ''), 'resolution': required_resolution(element)}
    with _lock:
        with open(journal_file, 'a') as f:
            f.write(json.dumps(entry) + '\n')
    start()


def start():
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, daemon=True)
        _thread.start()


def _loop():
    while True:
        time.sleep(check_every)
        try:
            if not _check():
                return  # nothing pending: the next download starts the thread again
        except Exception as e:
            ui_print('[verify] error: ' + str(e), ui_settings.debug)


def _library_items(since):
    # recently added movies and episodes with their files and plex guids
    import content.services.plex as plex
    items = []
    headers = {'Accept': 'application/json', 'X-Plex-Token': plex.users[0][1]}
    for kind in ('1', '4'):
        url = plex.library.url + '/library/all?type=' + kind + '&addedAt>>=' + str(int(since))
        for item in requests.get(url, headers=headers, timeout=60).json()['MediaContainer'].get('Metadata', []):
            guid = item.get('grandparentGuid') if kind == '4' else item.get('guid')
            files = [(part.get('file', ''), media.get('videoResolution')) for media in item.get('Media', []) for part in media.get('Part', [])]
            items.append((guid, files, item.get('grandparentTitle') or item.get('title')))
    return items


# the Offcloud sorter names movie folders without site prefixes like 'www.UIndex.org - '
site_prefix = regex.compile(r'^(?:www\.[a-z0-9.-]+\s*-\s*|\[[^\]]*\]\s*)+', regex.I)


def clean_name(name):
    return site_prefix.sub('', name).strip() or name


video_extensions = ('.mkv', '.mp4', '.avi', '.m4v', '.mov', '.ts', '.m2ts', '.mts', '.wmv', '.mpg', '.mpeg', '.webm', '.flv', '.vob', '.ogv')
mounts = {'OC': '/mnt/offcloud', 'RD': '/mnt/zurg/__all__'}


def _has_video(entry):
    # True/False when the download's folder (or single file) is on the mount, None while it isn't listed yet
    path = os.path.join(mounts.get(entry['service'], '/nonexistent'), entry['name'])
    try:
        if os.path.isfile(path):
            return path.lower().endswith(video_extensions)
        if not os.path.isdir(path):
            return None
        for root, dirs, files in os.walk(path):
            if any(f.lower().endswith(video_extensions) for f in files):
                return True
        return False
    except OSError:
        return None


def _reject(entry, reason):
    removed = _remove(entry)
    with _lock:
        try:
            wrong = json.load(open(wrong_file))
        except Exception:
            wrong = {}
        if entry['hash'] and entry['hash'] not in wrong.get(entry['expected'], []):
            wrong.setdefault(entry['expected'], []).append(entry['hash'])
        json.dump(wrong, open(wrong_file, 'w'))
    _rewatchlist(entry)
    ui_print('[verify] ' + reason + ' - ' + ('removed from ' + entry['service'] if removed else 'could not remove from ' + entry['service']) +
             ', will search again without it')
    entry['done'] = 'wrong'


def _remove(entry):
    if entry['service'] == 'RD':
        import debrid.services.realdebrid as rd
        r = requests.delete('https://api.real-debrid.com/rest/1.0/torrents/delete/' + entry['id'],
                            headers={'Authorization': 'Bearer ' + rd.api_key}, timeout=30)
        return r.status_code in (200, 204)
    if entry['service'] == 'OC':
        import debrid.services.offcloud as oc
        r = requests.get('https://offcloud.com/api/cloud/remove/' + entry['id'],
                         headers={'Authorization': 'Bearer ' + oc.api_key, 'Accept': 'application/json'}, timeout=30)
        return r.status_code == 200
    return False


def _rewatchlist(entry):
    # movies are removed from the watchlist after downloading; put them back so they are searched again
    import content.services.plex as plex
    if entry['type'] != 'movie':
        return
    for user in plex.users:
        requests.put('https://discover.provider.plex.tv/actions/addToWatchlist?ratingKey=' + entry['ratingKey'] +
                     '&X-Plex-Token=' + user[1], timeout=30)


def _check():
    with _lock:
        try:
            pending = [json.loads(line) for line in open(journal_file) if line.strip()]
        except FileNotFoundError:
            return False
    pending = [e for e in pending if not e.get('done')]
    if len(pending) == 0:
        return False
    items = _library_items(min(e['time'] for e in pending) - 120)
    for entry in pending:
        names = {entry['name'], clean_name(entry['name'])}
        mine = lambda f: any('/' + n + '/' in f or f.endswith('/' + n) for n in names)
        found = [(guid, title) for guid, files, title in items if any(mine(f) for f, r in files)]
        resolutions = [plex_resolution(r) for guid, files, title in items for f, r in files if mine(f)]
        if len(found) == 0:
            if time.time() - entry['time'] > 60 and _has_video(entry) is False:
                _reject(entry, 'no video file in ' + entry['title'] + ' (fake release?)')
                continue
            if time.time() - entry['time'] > give_up_after:
                ui_print('[verify] could not verify (not in Plex after 30 min): ' + entry['title'])
                entry['done'] = 'unverified'
            continue
        if any(guid == entry['expected'] for guid, title in found):
            # right title: an automatic download must also have the resolution its version was downloaded for
            known = [r for r in resolutions if r is not None]
            want = entry.get('resolution')
            if want and len(known) > 0 and want not in known:
                _reject(entry, 'wrong resolution: ' + entry['title'] + ' is ' + str(max(known)) + 'p in Plex, version "' +
                        entry.get('version', '') + '" needs ' + str(want) + 'p')
                continue
            ui_print('[verify] ok: ' + entry['title'] + ' is "' + str([t for g, t in found if g == entry['expected']][0]) + '"')
            entry['done'] = 'ok'
            continue
        identified = [(g, t) for g, t in found if str(g).startswith('plex://')]
        if len(identified) == 0:
            # Plex hasn't identified it (yet): that is no proof of a wrong title, keep waiting
            if time.time() - entry['time'] > give_up_after:
                ui_print('[verify] could not verify (Plex did not identify it): ' + entry['title'])
                entry['done'] = 'unverified'
            continue
        if time.time() - entry['time'] < 180:
            continue  # Plex sometimes refines its first match within a minute or two
        guid, title = identified[0]
        _reject(entry, 'wrong title: ' + entry['title'] + ' was identified by Plex as "' + str(title) + '" (' + str(guid) + '), not ' + entry['expected'])
    with _lock:
        try:
            current = [json.loads(line) for line in open(journal_file) if line.strip()]
        except FileNotFoundError:
            current = []
        status = {(e['service'], e['id']): e.get('done') for e in pending}
        keep = []
        for e in current:
            e['done'] = e.get('done') or status.get((e['service'], e['id']))
            if not e['done'] or time.time() - e['time'] < 86400:
                keep.append(e)
        with open(journal_file, 'w') as f:
            f.write(''.join(json.dumps(e) + '\n' for e in keep))
    return any(not e.get('done') for e in keep)
