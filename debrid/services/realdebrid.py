#import modules
from base import *
from ui.ui_print import *
import releases
from urllib.parse import urlparse, parse_qs
import base64

# (required) Name of the Debrid service
name = "Real Debrid"
short = "RD"
media_file_extensions = [
    '.yuv', '.wmv', '.webm', '.vob', '.viv', '.svi', '.roq', '.rmvb', '.rm',
    '.ogv', '.ogg', '.nsv', '.mxf', '.mts', '.m2ts', '.ts', '.mpg', '.mpeg',
    '.m2v', '.mp2', '.mpe', '.mpv', '.mp4', '.m4p', '.m4v', '.mov', '.qt',
    '.mng', '.mkv', '.flv', '.drc', '.avi', '.asf', '.amv'
]
# (required) Authentification of the Debrid service, can be oauth aswell. Create a setting for the required variables in the ui.settings_list. For an oauth example check the trakt authentification.
api_key = ""
# Define Variables
session = requests.Session()
errors = [
    [202," action already done"],
    [400," bad Request (see error message)"],
    [403," permission denied (infringing torrent or account locked or not premium)"],
    [503," service unavailable (see error message)"],
    [404," wrong parameter (invalid file id(s)) / unknown ressource (invalid id)"],
    [509," bandwidth limit exceeded"]
    ]
# Real-Debrid refuses torrents whose name contains WEB-DL, WEB.x264, WEB.H264, HDTV.x264 or HDTV.XviD (case-sensitive).
# Rule as of 2026-10-03, see https://github.com/elfhosted/litterbox - update this when RD changes it.
# Indexers rewrite separators in titles and magnet names, so separators are matched loosely while case is kept.
blocked_filename_regex = regex.compile(r'WEB[ ._-]?DL|WEB[ ._-]x264|WEB[ ._-]H264|HDTV[ ._-]x264|HDTV[ ._-]XviD')
# hashes of the torrents in the RD account; a release with one of these hashes is cached for sure
account_hashes = set()
account_hashes_time = 0
def get_account_hashes():
    global account_hashes, account_hashes_time
    if time.time() - account_hashes_time > 900:
        try:
            response = session.get('https://api.real-debrid.com/rest/1.0/torrents?limit=5000', headers={'authorization': 'Bearer ' + api_key}, timeout=30)
            if response.status_code == 200:
                account_hashes = {t['hash'].lower() for t in json.loads(response.content)}
                account_hashes_time = time.time()
        except Exception as e:
            ui_print('[realdebrid] could not read account torrents: ' + str(e), ui_settings.debug)
    return account_hashes
def hex_hash(info_hash):
    # magnets carry the info hash as 40 hex chars or 32 base32 chars; RD uses lowercase hex
    if len(info_hash) == 32:
        try:
            return base64.b32decode(info_hash.upper()).hex()
        except Exception:
            return ''
    return info_hash.lower()
def torrent_names(release):
    # the indexer title keeps the original case; the magnet dn is closer to the real torrent name but some indexers lowercase it
    names = [release.title]
    link = release.download[0] if hasattr(release, 'download') and len(release.download) > 0 else ''
    if isinstance(link, str) and link.startswith('magnet:'):
        names += parse_qs(urlparse(link).query).get('dn', [])
    return names
def setup(cls, new=False):
    from debrid.services import setup
    setup(cls,new)

# Error Log
def logerror(response):
    if not response.status_code in [200,201,204]:
        desc = ""
        for error in errors:
            if response.status_code == error[0]:
                desc = error[1]
        ui_print("[realdebrid] error: (" + str(response.status_code) + desc + ") " + str(response.content), debug=ui_settings.debug)
    if response.status_code == 401:
        ui_print("[realdebrid] error: (401 unauthorized): realdebrid api key does not seem to work. check your realdebrid settings.")
    if response.status_code == 403:
        ui_print("[realdebrid] error: (403 unauthorized): You may have attempted to add an infringing torrent or your realdebrid account is locked or you dont have premium.")

# Get Function
def get(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36','authorization': 'Bearer ' + api_key}
    response = None
    try:
        response = session.get(url, headers=headers)
        logerror(response)
        response = json.loads(response.content, object_hook=lambda d: SimpleNamespace(**d))
    except Exception as e:
        ui_print("[realdebrid] error: (json exception): " + str(e), debug=ui_settings.debug)
        response = None
    return response

# Post Function
def post(url, data):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36','authorization': 'Bearer ' + api_key}
    response = None
    try:
        ui_print("[realdebrid] (post): " + url + " with data " + repr(data), debug=ui_settings.debug)
        response = session.post(url, headers=headers, data=data)
        logerror(response)
        ui_print("[realdebrid] response: " + repr(response), debug=ui_settings.debug)
        response = json.loads(response.content, object_hook=lambda d: SimpleNamespace(**d))
    except Exception as e:
        if hasattr(response,"status_code"):
            if response.status_code >= 300:
                ui_print("[realdebrid] error: (json exception): " + str(e), debug=ui_settings.debug)
        else:
            ui_print("[realdebrid] error: (json exception): " + str(e), debug=ui_settings.debug)
        response = None
    return response

# Delete Function
def delete(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36','authorization': 'Bearer ' + api_key}
    try:
        ui_print("[realdebrid] (delete): " + url, debug=ui_settings.debug)
        response = requests.delete(url, headers=headers)
        logerror(response)

    except Exception as e:
        ui_print("[realdebrid] error: (delete exception): " + str(e), debug=ui_settings.debug)
        None
    return response

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

# (required) Download Function.
def download(element, stream=True, query='', force=False):
    cached = element.Releases
    if query == '':
        query = element.deviation()
    for release in cached[:]:
        # releases RD's check() did not take (e.g. blocked filenames) are left for other debrid services
        if not ('RD' in release.cached or 'RD' in release.maybe_cached) and not force:
            continue
        try:  # if release matches query
            if regex.match(query, release.title,regex.I) or force:
                time.sleep(5)  # RD throttles rapid adds (and may answer them with infringing_file)
                response = post('https://api.real-debrid.com/rest/1.0/torrents/addMagnet', {'magnet': release.download[0]})
                if hasattr(response, 'error') and response.error == 'too_many_requests':
                    ui_print(f'[realdebrid]: rate limited, retrying {release.title} in 2s')
                    time.sleep(2)
                    response = post('https://api.real-debrid.com/rest/1.0/torrents/addMagnet', {'magnet': release.download[0]})
                if hasattr(response, 'error') and response.error == 'infringing_file':
                    ui_print(f'[realdebrid]: torrent {release.title} marked as infringing... looking for another release.')
                    continue
                elif hasattr(response, 'error') and response.error == 'too_many_active_downloads':
                    ui_print(f'[realdebrid]: unable to add torrent {release.title} due to too many active downloads.')
                    continue
                elif not hasattr(response, "id"):
                    ui_print(f'[realdebrid]: unexpected error when adding torrent {release.title}: ' + (f'{getattr(response, "error", "")} (code {getattr(response, "error_code", "?")})' if response is not None else 'no response'))
                    continue
                time.sleep(1.0)
                torrent_id = str(response.id)
                response = get('https://api.real-debrid.com/rest/1.0/torrents/info/' + torrent_id)
                if response.status == 'magnet_error':
                    ui_print( f'[realdebrid]: failed to add torrent {release.title}. Looking for another release.')
                    delete('https://api.real-debrid.com/rest/1.0/torrents/delete/' + torrent_id)
                    continue
                if hasattr(response, "files") and len(response.files) > 0:
                    version_files = []
                    for file_ in response.files:
                        debrid_file = file(file_.id, file_.path, file_.bytes, release.wanted_patterns, release.unwanted_patterns)
                        version_files.append(debrid_file)
                    release.files = [version(version_files)]
                    cached_ids = [vf.id for vf in version_files if vf.wanted and not vf.unwanted and vf.name.endswith(tuple(media_file_extensions))]
                    if len(cached_ids) == 0:
                        ui_print('[realdebrid] no selectable media files.', ui_settings.debug)
                    else:
                        post('https://api.real-debrid.com/rest/1.0/torrents/selectFiles/' + torrent_id, {'files': ",".join(map(str, cached_ids))})
                        ui_print('[realdebrid] selectFiles response ' + repr(response), ui_settings.debug)

                    response = get('https://api.real-debrid.com/rest/1.0/torrents/info/' + torrent_id)
                    actual_title = ""
                    if len(response.links) == len(cached_ids) and len(cached_ids) > 0:
                        actual_title = response.filename
                        release.download = response.links
                    else:
                        if response.status in ["queued", "magnet_conversion", "downloading", "uploading"]:
                            if hasattr(element, "version"):
                                debrid_uncached = True
                                for i, rule in enumerate(element.version.rules):
                                    if (rule[0] == "cache status") and (rule[1] == 'requirement' or rule[1] == 'preference') and (rule[2] == "cached"):
                                        debrid_uncached = False
                                if debrid_uncached:
                                    import debrid as db
                                    db.downloading += [element.query() + ' [' + element.version.name + ']']
                                    ui_print('[realdebrid] added uncached release: ' + release.title)
                                    return True
                                else:
                                    ui_print(f'[realdebrid]: {release.title} is in {response.status} status (not cached). Looking for another release.')
                                    delete('https://api.real-debrid.com/rest/1.0/torrents/delete/' + torrent_id)
                                    continue
                        else:
                            ui_print(f'[realdebrid]: {release.title} is in status [{response.status}] - trying a different release.')
                            delete('https://api.real-debrid.com/rest/1.0/torrents/delete/' + torrent_id)
                            continue
                    if response.status == 'downloaded':
                        ui_print('[realdebrid] added cached release: ' + release.title)
                        if actual_title != "":
                            release.title = actual_title
                        return True

                else:  # no files found after adding torrent
                    if response.status == 'downloading':
                        if hasattr(element, "version"):
                            import debrid as db
                            db.downloading += [element.query() + ' [' + element.version.name + ']']
                        ui_print('[realdebrid] added uncached release: ' + release.title)
                        return True
                    else:
                        ui_print(f'[realdebrid]: no files found for torrent {release.title} in status {response.status}. looking for another release.')
                        delete('https://api.real-debrid.com/rest/1.0/torrents/delete/' + torrent_id)
                        continue

                ui_print('[realdebrid] added uncached release: ' + release.title)
                return True
            else:
                ui_print(f'[realdebrid] error: rejecting release: "{release.title}" because it doesnt match the allowed deviation "{query}"')
                ui_print(f'[realdebrid] if this was a mistake, you can manually add it: "{release.download[0]}"')
        except Exception as e:
            ui_print(f'[realdebrid] unexpected error: ' + str(e))
    return False

# (required) Check Function
def check(element, force=False):
    if force:
        wanted = ['.*']
    else:
        wanted = element.files()
    unwanted = releases.sort.unwanted
    wanted_patterns = list(zip(wanted, [regex.compile(r'(' + key + ')', regex.IGNORECASE) for key in wanted]))
    unwanted_patterns = list(zip(unwanted, [regex.compile(r'(' + key + ')', regex.IGNORECASE) for key in unwanted]))
    skipped = 0
    known_cached = 0
    library = get_account_hashes()
    for release in element.Releases[:]:
        if any(blocked_filename_regex.search(n) for n in torrent_names(release)):
            # not offered to RD (no 'RD' in cached/maybe_cached); other debrid services may still take it
            skipped += 1
            continue
        release.wanted_patterns = wanted_patterns
        release.unwanted_patterns = unwanted_patterns
        if hex_hash(getattr(release, 'hash', '')) in library:
            if 'RD' not in release.cached:
                release.cached += ['RD']
                known_cached += 1
        else:
            release.maybe_cached += ['RD']  # we won't know if it's cached until we attempt to download it
    if known_cached > 0:
        ui_print(f'[realdebrid] {known_cached} releases already in your RD account (cached)')
    if skipped > 0:
        ui_print(f'[realdebrid] skipped {skipped} releases matching RD filename filter')
