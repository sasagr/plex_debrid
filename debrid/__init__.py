from base import *
#import child modules
from debrid import services
from debrid import verify
from ui.ui_print import *

tracker = []
downloading = []
uncached = 'true'

# Download Method:
def download(element, stream=True, query='', force=False):
    downloaded_files = []
    if stream:
        cached_releases = copy.deepcopy(element.Releases)
        downloaded = False
        for release in cached_releases:
            element.Releases = [release, ]
            if len(tracker) > 0:
                for t, s in tracker:
                    if regex.search(t, release.source, regex.I):
                        release.cached = s
            for service in services.get():
                if service.short in release.cached + release.maybe_cached:
                    if service.download(element, stream=stream, query=query, force=force):
                        if not force:  # only automatic downloads are checked; manual picks (scraper menu) are never touched
                            verify.record(element, element.Releases[0], service.short)
                        downloaded = True
                        downloaded_files += element.Releases[0].files
                        if not hasattr(element,"existing_releases"):
                            element.existing_releases = []
                        if not hasattr(element,"downloaded_releases"):
                            element.downloaded_releases = []
                        element.existing_releases += [element.Releases[0].title]
                        element.downloaded_releases += [element.Releases[0].title]
                        break
            if downloaded:
                break
        if len(element.Releases) > 0:
            element.Releases[0].files = downloaded_files
        return downloaded
    else:
        scraped_releases = copy.deepcopy(element.Releases)
        downloaded = False
        for release in scraped_releases:
            element.Releases = [release, ]
            if len(tracker) > 0:
                for t, s in tracker:
                    if regex.search(t, release.source, regex.I):
                        release.cached = s
            for service in services.get():
                if len(release.cached) > 0:
                    if service.short in release.cached + release.maybe_cached:
                        if service.download(element, stream=stream, query=query, force=force):
                            downloaded = True
                            downloaded_files += element.Releases[0].files
                            if not hasattr(element, "existing_releases"):
                                element.existing_releases = []
                            if not hasattr(element, "downloaded_releases"):
                                element.downloaded_releases = []
                            element.existing_releases += [element.Releases[0].title]
                            element.downloaded_releases += [element.Releases[0].title]
                            break
                else:
                    if service.download(element, stream=stream, query=query, force=force):
                        downloaded = True
                        downloaded_files += element.Releases[0].files
                        if not hasattr(element,"existing_releases"):
                            element.existing_releases = []
                        if not hasattr(element,"downloaded_releases"):
                            element.downloaded_releases = []
                        element.existing_releases += [element.Releases[0].title]
                        element.downloaded_releases += [element.Releases[0].title]
                        break
            if downloaded:
                break
        if len(element.Releases) > 0:
            element.Releases[0].files = downloaded_files
        return downloaded

# Check Method:
def check(element, force=False):
    if len(element.Releases) == 0:
        return
    checked = []
    for release in element.Releases[:]:
        if release.checked:
            checked += [release]
            element.Releases.remove(release)
    # releases Plex identified as a different title earlier are never offered again for this item
    verify.start()  # picks up downloads recorded before a restart (stops by itself when nothing is pending)
    wrong = verify.wrong_hashes(element) if not force else set()
    if len(wrong) > 0:
        skipped = [r for r in element.Releases if verify.hex_hash(getattr(r, 'hash', '')) in wrong]
        for release in skipped:
            element.Releases.remove(release)
        if len(skipped) > 0:
            ui_print('[verify] skipped ' + str(len(skipped)) + ' releases identified as a different title before')
    activeservices = services.active
    if len(element.Releases) > 0:
        ui_print("checking scraped releases on: [" + "],[".join(activeservices) + "] ...")
        for service in services.get():
            service.check(element, force=force)
        ui_print("done")
    for release in checked:
        element.Releases += [release]
    for release in element.Releases:
        release.checked = True
