#import modules
from base import *
from ui.ui_print import *
import releases

# (required) Name of the Debrid service
name = "Offcloud"
short = "OC"
# (required) Authentification of the Debrid service. Set via the "Offcloud API Key" setting (stored in settings.json, never in code).
api_key = ""
# Define Variables
session = requests.Session()
base_url = 'https://offcloud.com'
# cache/info batch size; Offcloud documents no limit, keep requests small
cache_batch = 50
# seconds to wait for a cached transfer to reach "downloaded"
download_wait = 30


def setup(cls, new=False):
    from debrid.services import setup
    setup(cls, new)


# Request Function: JSON in, JSON out, None on any error
def request(method, path, payload=None, timeout=60):
    headers = {'Authorization': 'Bearer ' + api_key, 'Accept': 'application/json'}
    for attempt in range(2):
        try:
            ui_print("[offcloud] (" + method + "): " + path, debug=ui_settings.debug)
            response = session.request(method, base_url + path, headers=headers, json=payload, timeout=timeout)
            if response.status_code == 429 and attempt == 0:
                wait = min(int(response.headers.get('Retry-After', '5')), 30)
                ui_print("[offcloud] rate limited, retrying in " + str(wait) + "s")
                time.sleep(wait)
                continue
            if response.status_code in [401, 403]:
                ui_print("[offcloud] error: (" + str(response.status_code) + ") offcloud api key does not seem to work. check your offcloud settings.")
                return None
            if response.status_code >= 300:
                ui_print("[offcloud] error: (" + str(response.status_code) + ") " + response.text[:200])
                return None
            return response.json()
        except Exception as e:
            ui_print("[offcloud] error: (request exception): " + str(e), debug=ui_settings.debug)
            return None
    return None


# Object classes
class file:
    def __init__(self, id, name, size, wanted_list, unwanted_list):
        self.id = id
        self.name = name
        self.size = size / 1000000000
        self.match = ''
        wanted = False
        unwanted = False
        for key, wanted_pattern in wanted_list:
            if wanted_pattern.search(self.name):
                wanted = True
                self.match = key
                break
        if not wanted:
            for key, unwanted_pattern in unwanted_list:
                if unwanted_pattern.search(self.name) or self.name.endswith('.exe') or self.name.endswith('.txt'):
                    unwanted = True
                    break
        self.wanted = wanted
        self.unwanted = unwanted

    def __eq__(self, other):
        return self.id == other.id


class version:
    def __init__(self, files):
        self.files = files
        self.needed = 0
        self.wanted = 0
        self.unwanted = 0
        self.size = 0
        for file in self.files:
            self.size += file.size
            if file.wanted:
                self.wanted += 1
            if file.unwanted:
                self.unwanted += 1


def is_magnet(release):
    return hasattr(release, 'download') and len(release.download) > 0 and str(release.download[0]).startswith('magnet:')


def history():
    response = request('GET', '/api/cloud/history')
    if isinstance(response, dict):
        response = response.get('history', [])
    return response if isinstance(response, list) else []


def wait_until_downloaded(request_id):
    start = time.time()
    while time.time() - start < download_wait:
        for item in history():
            if item.get('requestId') == request_id:
                if item.get('status') in ['downloaded', 'error', 'canceled']:
                    return item.get('status')
        time.sleep(3)
    return 'timeout'


# (required) Download Function. Only adds releases that Offcloud reported as cached: the API has no delete,
# so a failed or uncached transfer could not be cleaned up again.
def download(element, stream=True, query='', force=False):
    if not stream:
        ui_print('[offcloud] uncached downloads are not supported.', ui_settings.debug)
        return False
    cached = element.Releases
    if query == '':
        query = element.deviation()
    for release in cached[:]:
        try:
            if not ('OC' in release.cached and is_magnet(release)):
                continue
            if not (regex.match(query, release.title, regex.I) or force):
                ui_print(f'[offcloud] error: rejecting release: "{release.title}" because it doesnt match the allowed deviation "{query}"')
                continue
            # the same torrent may already be in the cloud (e.g. added earlier or manually)
            release_hash = release.hash.lower()
            existing = [item for item in history() if release_hash and release_hash in str(item.get('originalLink', '')).lower()]
            if len(existing) > 0 and existing[0].get('status') == 'downloaded':
                ui_print('[offcloud] release already in your offcloud cloud: ' + release.title)
                return True
            response = request('POST', '/api/cloud', {'url': release.download[0]})
            if not isinstance(response, dict) or 'requestId' not in response:
                reason = response.get('error', response.get('not_available', '')) if isinstance(response, dict) else 'no response'
                ui_print(f'[offcloud]: unexpected error when adding torrent {release.title}: {reason}')
                continue
            status = response.get('status')
            if status != 'downloaded':
                status = wait_until_downloaded(response['requestId'])
            if status == 'downloaded':
                ui_print('[offcloud] added cached release: ' + release.title)
                return True
            ui_print(f'[offcloud]: {release.title} is in status [{status}] after adding - looking for another release.')
        except Exception as e:
            ui_print('[offcloud] unexpected error: ' + str(e))
    return False


# (required) Check Function: Offcloud can report the cache status of magnets without adding them
def check(element, force=False):
    if force:
        wanted = ['.*']
    else:
        wanted = element.files()
    unwanted = releases.sort.unwanted
    wanted_patterns = list(zip(wanted, [regex.compile(r'(' + key + ')', regex.IGNORECASE) for key in wanted]))
    unwanted_patterns = list(zip(unwanted, [regex.compile(r'(' + key + ')', regex.IGNORECASE) for key in unwanted]))
    candidates = [release for release in element.Releases if is_magnet(release) and 'OC' not in release.cached]
    found = 0
    for offset in range(0, len(candidates), cache_batch):
        batch = candidates[offset:offset + cache_batch]
        response = request('POST', '/api/cache/info', {'urls': [release.download[0] for release in batch], 'includeFiles': True})
        if not isinstance(response, list) or len(response) != len(batch):
            ui_print('[offcloud] error: unexpected cache check response, skipping ' + str(len(batch)) + ' releases')
            continue
        for release, info in zip(batch, response):
            if not isinstance(info, dict) or not info.get('cached'):
                continue
            version_files = []
            for index, file_ in enumerate(info.get('files', [])):
                path = '/'.join(file_.get('folder', []) + [file_.get('filename', '')])
                version_files.append(file(index, path, file_.get('size', 0) or 0, wanted_patterns, unwanted_patterns))
            if len(version_files) > 0:
                release.files = [version(version_files)]
                release.wanted = release.files[0].wanted
                release.unwanted = release.files[0].unwanted
            release.cached += ['OC']
            found += 1
    if len(candidates) > 0:
        ui_print(f'[offcloud] {found} of {len(candidates)} releases cached on offcloud')
