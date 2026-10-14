# The redirect-instead-of-proxy pattern (built 26 Sep 2026, running on the Pi)

Context for reuse on **plex_debrid**: the goal there is to stop a local Plex server spending its
bandwidth on bytes that a debrid CDN could send to the client directly.

## What exists today

`webdav-redirect.py` — ~150 lines, Python stdlib only — on the Raspberry Pi (`pi2`) at
`/opt/webdav-redirect/`, as `webdav-redirect.service`, listening on `:8081`, published at
`webdav.sasagr.uk` through its own `cloudflared-webdav` tunnel.

It sits in front of **rclone's WebDAV bridge to Dropbox** (`Dropbox:/PersonalVideos`, read-only),
which was moved to `127.0.0.1:8082` so the proxy could take the public port without any client
being reconfigured.

## The logic, in five steps

1. **Everything except the bytes is forwarded untouched** — authentication, `PROPFIND` listings,
   `HEAD`, `OPTIONS`. The backend stays the single source of truth; the proxy adds no state.
2. **Only `GET` on a file is intercepted.**
3. **Authorise first, redirect second.** The proxy re-checks the client's credentials *and* that
   the file exists by issuing a `Depth: 0` PROPFIND to rclone. A link is never handed out on the
   strength of the request alone.
4. **Answer `302` to a short-lived provider link** (a Dropbox temporary link). The video bytes
   then go provider → client and never touch the Pi.
5. **Keep a fallback.** Clients known not to follow redirects (Finder, Windows' built-in WebDAV)
   and any request carrying `?proxy=1` are streamed through the backend exactly as before.

## What made it work in practice

- **Port swap, not reconfiguration** — the proxy took the port the clients already knew.
- **Credential reuse** — it borrows rclone's existing Dropbox token and asks rclone to refresh it
  when expired, so there is no second place keeping secrets.
- **Logging is how you prove it.** Every request is logged (since 3 Oct, listings too). A
  `PROXY GET` line for a video means *the client did not follow the redirect* and fell back —
  that single line is the whole diagnostic.

## Carrying it to Plex — what transfers and what has to be proven

Transfers cleanly: the shape (control plane through the server, bytes redirected), authorise
before redirect, per-client fallback, and the log line that tells you which path was taken.

Must be established before building anything:

- **Plex clients fetch media from PMS at `/library/parts/<id>/file.ext` with a token.** A proxy in
  front of PMS would have to 302 exactly those requests. *Whether each Plex client follows a
  cross-origin 302 there is the open question, and it decides the whole idea.* Test one client
  before writing the mapping.
- **It can only help Direct Play.** If PMS decides to transcode, the bytes must pass through it by
  definition and no redirect can avoid that.
- **The redirect target must honour `Range`** (debrid CDNs do; it is what seeking needs).
- **The real work is the mapping**: part id → the file on the rclone/zurg mount → the debrid link.
  In the Dropbox version that step was free, because the path the client asked for *was* the path
  rclone knew. Plex interposes its own ids, so something has to resolve them.
- **Links expire.** Fine for a redirect answered per request; not fine if anything caches them.
