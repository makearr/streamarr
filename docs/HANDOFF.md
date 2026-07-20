# HANDOFF.md — Architecture and implementation notes

Companion to `REQUEST.md`. Read both before changing anything.

## 1. High-level architecture

```
streamarr/
├── __init__.py        APP_NAME, VERSION ("1.0.0.0" — policy: never bump 1.0 without permission)
├── config.py          yml config in $STREAMARR_CONFIG_DIR/config.yml, defaults, merge, save,
│                      normalize_url (scheme-optional URLs), proxy_url()
├── db.py              SQLite ($CONFIG/streamarr.db, WAL, thread-local conns):
│                      cache_items / stats / jobs tables + helpers; db.stat_hook feeds prometheus
├── runtime.py         log ring buffer (2000 entries) + BufferHandler; log_connection_error()
│                      (walks exception cause chain — the "verbose connection failure" requirement);
│                      status-bar state (set_status/clear_status/get_status);
│                      RateLimiter (per-provider spacing + exponential backoff) as global `limiter`
├── naming.py          SxxEyy/Staffel/Folge regexes, release_title() for the three schemes,
│                      quality_tag()
├── indexers.py        indexer lookup, channel-cache refresh, unified search(),
│                      Newznab caps/RSS XML rendering
├── downloader.py      single worker thread; queue engine (enqueue/pause/resume/delete/move/
│                      speed limit); yt-dlp download with progress hooks; SponsorBlock;
│                      audio extraction; recovery of stuck jobs on start
├── arr.py             httpx clients for arr APIs (v3/v1 map), test() with per-failure-type
│                      diagnostics, configure() = upsert Newznab indexer + Sabnzbd client
├── auth.py            pbkdf2 password hash, itsdangerous session cookie (14d), api-key check,
│                      require_ui / require_api_key dependencies
├── maintenance.py     prometheus counters/gauge, yt-dlp version + pip self-update,
│                      5-min loop: cache prune, auto-update, metric refresh
├── providers/
│   ├── site.py        generic yt-dlp provider: SITE_PRESETS (bbc…youporn+custom), source-list
│   │                  enumeration with ordinals, template-based search ({query} / scsearch)
│   ├── ardzdf.py      ARD/ZDF direct search APIs with MediathekViewWeb channel-filter fallback
│   ├── youtube.py     yt-dlp flat extraction: list_channel (reversed → ordinal = upload order),
│   │                  broad_search (ytsearchN:), format_string() fallback chain, rate-limit detect
│   └── mediathek.py   MediathekViewWeb POST (Content-Type: text/plain!), HD-first URL pick,
│                      429/503 → limiter.penalize
├── api/
│   ├── newznab.py     GET /newznab/{id}/api (t=caps|search|tvsearch|movie|music, season/ep filter)
│   │                  GET /newznab/{id}/download/{item}.nzb → pseudo-NZB
│   ├── sabnzbd.py     GET+POST /sabnzbd/api — all modes the arr SAB client uses
│   └── ui.py          /ui/* JSON API for the SPA (auth, queue, search, settings, indexers,
│                      instances, stats, logs, ytdlp update, backup/restore)
├── main.py            FastAPI lifespan wiring, routers, /ping, /metrics, /static, SPA catch-all
└── static/            vanilla-JS SPA (index.html, app.css, app.js, logo.svg) — no build step
```

Supporting files: `Dockerfile`, `entrypoint.sh` (lsio env handling + gosu drop),
`healthcheck.py`, `docker-compose.yml`, `requirements.txt`, `tests/` (31 pytest cases).

## 2. The pseudo-NZB mechanism (core trick)

Newznab results link to `/newznab/{indexer}/download/{item_id}.nzb`. That endpoint serves a
**valid NZB XML wrapper** whose only payload is an XML comment:

```
<!-- STREAMARR:{"streamarr":true,"indexer_id":…,"item_id":…,"name":…,"url":…,"provider":…,"media":…} -->
```

The arr app stores/forwards this file to its SAB client (Streamarr) via `mode=addfile`.
`sabnzbd.py:_add_from_nzb` extracts the JSON with a regex and enqueues a real job.
Foreign NZBs (no marker) are rejected — Streamarr never talks to Usenet.

Consequence: **cache items must exist when the NZB is fetched** (`db.cache_get`). Mediathek
search results are upserted into the cache at search time for exactly this reason.

## 3. Requirement → implementation map

| Requirement | Where |
|-------------|-------|
| Newznab API per indexer | `api/newznab.py`, XML in `indexers.py` |
| SABnzbd API | `api/sabnzbd.py` (version, get_config w/ categories, fullstatus, queue+actions, pause, resume, switch, config/speedlimit, history+delete, addfile; addurl rejected by design) |
| Multiple indexers, shared queue | `config.indexers[]` list; one `downloader` worker |
| Absolute/date/auto naming | `naming.release_title`; ordinal assigned in `youtube.list_channel` (entries reversed so ordinal 1 = oldest upload) |
| Quality/format limits | `config.quality` → `youtube.format_string` fallback chain; audio via FFmpegExtractAudio |
| Video-or-audio | per-indexer `media` field |
| Rate limit + backoff | `runtime.RateLimiter`; providers call `wait/penalize/reset`; UI shows backoff pill |
| Auto-configure arr | `arr.configure` — upserts by name; per-arr SAB field names in `_category_fields` (tvCategory/movieCategory/musicCategory) |
| Verbose connection logs | `runtime.log_connection_error` + `arr.test` per-exception-type messages (TCP vs timeout vs 401 vs other HTTP) |
| Backend-unavailable banner | SPA polls `/ui/health/instances` every 30 s |
| Status bar | `runtime` status state; SPA polls `/ui/status` every 2 s |
| Opposite-theme logo badge | CSS var `--logo-badge` set inversely in both themes (`app.css`) |
| URL with/without scheme | `config.normalize_url` |
| TLS validation toggle | per instance `verify_ssl` → httpx `verify=` |
| Proxy | `config.proxy_url()` → httpx `proxy=` and yt-dlp `proxy` opt (http/https/socks4/socks5; httpx[socks] installed) |
| Auth | `auth.py`; ALL `/ui`, `/newznab`, `/sabnzbd` routes protected; static SPA is public by design |
| Non-root + PUID/PGID/UMASK/TZ | `entrypoint.sh` (root → chown → gosu streamarr); Dockerfile user 1000 |
| Healthcheck | `/ping` + `healthcheck.py` + Dockerfile HEALTHCHECK |
| Cache + stats in /config | `db.py` (`streamarr.db`) |
| Prometheus | `maintenance.py`, `/metrics` |
| Backup/restore | `ui.py` (zip of config.yml + streamarr.db) |
| yt-dlp self-update | `maintenance.update_ytdlp` → `pip install --upgrade --user` into `/app/.local` (on PATH/PYTHONPATH via Dockerfile); auto mode in the 5-min loop |
| SponsorBlock | `downloader._ydl_download` postprocessors, YouTube only, off by default |
| Version policy | `__init__.VERSION` |

## 3b. Round-2 additions (v1.0.1.0)

- **Auth modes** (`streamarr.auth.mode`): `none` (default — open), `local` (RFC1918/loopback
  bypass, login from elsewhere), `forms`. No password requirements. `auth.needs_setup` only in
  forms mode. `/ui/auth/mode`, `/ui/auth/setup` (idempotent — also changes the account),
  `/ui/apikey/rotate` (spawns `arr.sync_all` to re-push the key).
- **Naming schemes**: added `sxxeyy` (strict — untagged items filtered in `indexers.search`)
  and `arr` (newznab layer stores `_arr_se` from the request's season/ep on filter-matched
  items; `naming.release_title` prefers it). `_filter_episode` now returns `(items, matched)`.
- **Instance auto-sync**: `INSTANCE_DEFAULTS` gained `auto_configure`, `own_url`,
  `indexer_ids`. `arr.sync_instance/sync_all`; called on startup (main lifespan thread),
  after `/ui/instances` save, and after key rotation.
- **Providers**: `providers/site.py` presets — search templates only where yt-dlp reliably
  extracts the site's search/listing pages (pornhub, xhamster, xvideos, youporn; soundcloud
  via native `scsearch`); everything else is source-list mode (any listable URL). BBC preset
  mirrors iplayarr's programme-list approach. `providers/ardzdf.py` hits
  api.ardmediathek.de / zdf-prod-futura.zdf.de with defensive parsing; ANY failure falls back
  to MediathekViewWeb filtered by channel name. `youtube._extract` gained a `provider` kwarg
  so each preset gets its own rate-limit/backoff bucket.
- **Manual download**: `/ui/download` (any URL, provider="manual"); dashboard hero treats
  pasted `http(s)://` input as a download, anything else as a search.
- **Dashboard**: `/ui/dashboard` aggregate endpoint; SPA landing route is now #dashboard.
- **UI**: preset-card add-indexer flow (`/ui/presets`), Security settings panel (mode select,
  account, key copy/rotate), season/episode inputs on manual search.

## 3c. Round-3 additions (v1.0.2.0)

- **public_url + candidate guessing**: `config.public_url_candidates(own_url)` → [own_url,
  streamarr.public_url, http://streamarr:PORT (docker DNS default), http://<hostname>:PORT,
  http://<container-ip>:PORT]. `arr.configure` tries each; the arr's own create-validation
  (HTTP 400 = unreachable) advances to the next candidate. Frontend prompts only if all fail.
  Verified in tests/test_arr_configure.py against a validating mock arr (create, upsert,
  fallback, Readarr fields, bad-key short-circuit).
- **Per-indexer quality**: `indexer.quality` overrides merged via `config.quality_for(idx)`;
  used by newznab tags, manual search and the downloader (resolved at download time).
- **Proxy bypass**: `config.proxy_for(url)` — RFC1918/loopback/bare-hostname bypass
  (`bypass_local`) + fnmatch wildcards in `ignored_addresses`. All outbound callers use it.
- **yt-dlp auto-update**: on by default; startup update (maintenance.startup_update, thread);
  interval updates postponed while any job is Downloading; when pip reports
  "Successfully installed yt-dlp-X" and `restart_after_update`, restart_when_idle() waits for
  downloads then os._exit(3) → docker restart policy reloads the new version. No restart loop:
  a fresh boot finds pip already satisfied (changed=False).
- **YouTube dates**: extractor_args youtubetab:approximate_date=timestamp gives (approximate)
  upload timestamps in flat listings; broad search prefers entry timestamp over now().
- **Import recognition**: release names end in `-Streamarr` (release group) — helps arr parsers.
- **Readarr**: API v1; SAB fields bookCategory/recentBookPriority/olderBookPriority, cat "books".
- **Queue**: per-job priority (Force/High/Normal/Low → 2/1/0/-1, `/ui/queue/priority`),
  client-side sortable columns, recent-history block on the queue page. SAB categories now
  include books/audiobooks/podcasts.
- **Audio presets**: site presets `podcast` (mp3, cats 3000/3010) and `audiobook`
  (m4b, cats 3030/7000) with per-preset audio_format quality override.
- **Auth mode default** stays `none`; account form has no password rules.
- App shell served no-store with versioned asset URLs (`?v=VERSION`) — stale-cache login
  screens can't recur.

## 3d. Round-4 additions (v1.0.3.0)

- **CRITICAL FIX — download client auto-config**: arr apps call the SAB client at
  `{host}:{port}/{urlBase}/api`. With urlBase "" that hit the SPA catch-all, which returned
  HTML → "Unknown Version" / "Unexpected character '<'". Fixed twice: the pushed download
  client now sets `urlBase: "sabnzbd"`, and `/api` is additionally registered as an alias of
  the SAB endpoint for manually configured clients. The mock arr in
  tests/test_arr_configure.py now rejects any download client without urlBase=sabnzbd, so a
  regression fails the suite.
- **Subscriptions** (`streamarr/subscriptions.py`): per-indexer `subscriptions` list
  (youtube/site providers). Periodic run (config `subscriptions.interval_minutes`, loop in
  maintenance) lists each source; first check sets a baseline (backlog marked seen in
  `subs_seen`, nothing downloaded); later checks enqueue only new items. Optional
  `subscriptions.check_arr`: before enqueueing, `arr.has_release` asks sonarr/whisparr
  `/parse?title=` — if all parsed episodes have files, the item is skipped and marked seen.
  Category derives from the indexer's first newznab category. Manual trigger:
  `/ui/subscriptions/run` + Settings button.
- **Copy log** button on the Logs page (clipboard, with execCommand fallback).

## 3e. Round-5 additions (v1.0.4.0)

- **Sonarr episode matching (Veritasium case)**: TVDB maps YouTube channels to year-seasons
  (S2026E17) — un-derivable from upload order or dates. With naming "arr" and season/ep in a
  tvsearch, `newznab._arr_title_match` asks each sonarr/whisparr instance via
  `arr.find_episode` (/series cached 5 min + /episode) for the requested episode's TITLE,
  fuzzy-matches it against cached item titles (`arr._title_match`: normalize, containment or
  ≥0.6 token overlap) and returns exactly that item renamed to the requested SxxEyy.
  Regression-tested against the mock arr with the literal Veritasium data. New YouTube
  indexers default to naming "arr"; existing configs must switch manually.
- **Exact upload dates**: `youtube.video_details` (single full extraction) +
  `ensure_exact_date` update the cache with the real upload date and set meta.exact_date.
  Called at NZB download, manual grab, and subscription enqueue — list-time approximate
  dates only affect sorting, never release names.
- **Debug logging**: every Newznab request (client, t, q, season/ep, cat) + response summary
  (count, first 3 release names) and explicit "NO MATCH" episode-filter diagnostics;
  SAB requests (non-poll modes); yt-dlp extraction counts; per-search result counts.
- **First-class subscriptions** (config `subs`, /ui/subs CRUD, own nav page replacing
  History): per subscription id/title/url/provider(+preset)/media/naming/category/
  **individual storage path** (jobs carry `outdir`; downloader honours it)/**individual
  check interval** (gated via subs_runs.last_run inside due_subs; maintenance loop ticks
  every 300 s, so effective minimum ≈5 min). Legacy indexer-level subscriptions keep working.
- **History page removed** from nav (#history redirects to #queue); queue's merged
  recent-history block now shows 25 entries.

## 3f. Round-6 additions (v1.0.5.0)

- **Automatic-download fixes** (from a production log analysis):
  1. *Second-chance search*: when the arr-resolved episode title isn't among the
     series-name search results (older videos fall off YouTube search relevance),
     `_arr_title_match` re-searches the indexer for the episode title itself before
     giving up. Verified in tests with a stubbed search.
  2. *Empty feed on no-match*: an unmatched season/ep request now returns an EMPTY RSS
     instead of the full unfiltered list — the arr previously churned on dozens of
     date-named garbage releases ("Processing Release …").
  3. *NZB name identity* (crucial): the arr-resolved S<year>E<n> only existed in the search
     response; the NZB (= SAB job = completed folder name) fell back to date naming, so the
     import couldn't map the file. The match now persists `meta.arr_se` in the cache and the
     NZB endpoint applies it — search result, job and folder all carry the same name.
- **Subscriptions**: site dropdown now lists YouTube plus every site preset directly
  (`site::<preset>` in the UI, mapped to provider+site_preset); hint links to the Indexers
  page for URL formats. Per-subscription `check_arr` override: "" inherit | "on" | "off".

## 3g. Round-7 additions (v1.0.6.0) — the "no downloads" root cause

- **ROOT CAUSE**: Sonarr validates every downloaded NZB (NzbValidationService) and rejects
  files without <file>/<segments> — "Invalid NZB: No files". The pseudo-NZB was an empty
  wrapper, so every grab silently died between "fetch NZB" and "push to download client"
  (visible in logs as: NZB served, then no addfile, then Sonarr re-searching the same
  episode). The NZB now contains a structurally valid head/file/groups/segments block
  (subject "<name>" yEnc (1/1), positive segment bytes, message-id) around the STREAMARR
  payload comment. tests/test_grab_chain.py re-implements Sonarr's validation rules and
  runs the full chain: search → validate → addfile → queue name assertion.
- **Second bug caught by the live trial run**: the yt-dlp format chain had no unconditional
  fallback, so direct-URL sources without format metadata (Mediathek MP4s, many site
  providers, the trial's plain HTTP file) failed with "Requested format is not available".
  Both video and audio chains now end in /best.
- **Series-aware candidate picking**: identical titles from wrong channels (e.g. music
  "…- Topic" uploads) no longer win: title matches are filtered by series/channel match;
  a single cross-series hit is accepted with a warning; ambiguous cross-series hits are
  rejected. (Production log: 'Germany Is Over' by 'Epic Mountain - Topic'.)
- **Verified end-to-end in a live trial**: real HTTP host, Sonarr-sequence replay, worker
  completed a download to /downloads/tv/<release>/<release>.mp4 with the exact
  S01E001-style name the arr imports.

## 3h. Round-8 (v1.0.7.0) — the 480p bug

- Root cause reproduced deterministically (tests/test_format_selection.py, real yt-dlp
  selector against a synthetic format table): modern YouTube serves avc1/mp4 only up to
  480p on many videos; HD exists only as vp9/av01. The hard filter
  `bestvideo[ext=mp4]` therefore SUCCEEDED with 480p, so later fallback selectors never
  ran. Fix: `youtube.format_opts` — format "bv*+ba/b" with
  format_sort ["res:<max>", "fps:<max>", "vext:<container>", "aext:m4a"]; sorting expresses
  preferences without excluding formats, so the resolution cap always wins and the container
  preference only breaks ties. Downloads are remuxed into the configured container
  (vp9-in-mp4 is fine). The legacy `format_string` remains for reference/tests.
- The downloader now logs the chosen format after every download:
  "Downloaded format for '<name>': <ids> (<resolution>)".

## 3i. Round-9 (v1.0.8.0) — Mediathek indexer push failure

- Sonarr's indexer test performs a recent-releases query with NO search term. For
  mediathek/ard/zdf that reached MediathekViewWeb with `queries: [{query: ""}]` — and an
  empty-string query matches NOTHING on MVW, so the test saw an empty (but valid) feed:
  "Query successful, but no results in the configured categories". Fix:
  `mediathek.build_body` omits the queries clause entirely when the text is empty (MVW then
  returns the latest entries), and ARD/ZDF skip their direct search APIs (which need a term)
  for empty queries, going straight to the MVW-latest channel-filtered fallback.
- RSS items now carry parent AND standard subcategory attrs/elements (5000+5040, 2000+2040,
  3000+3010) via `indexers._expand_cats`, so arr apps configured with subcategory defaults
  still see matching releases.

## 3j. Round-10 (v1.0.9.0) — season requests + secret transport

- **Season requests**: `t=tvsearch&season=X` without ep is expanded into single-episode
  releases. With naming "arr": `arr.season_episodes` lists the season's episodes and each is
  title-matched against the search results (bounded to 5 second-chance searches per request);
  every hit is renamed SxxxxEyyy and its identity persisted (meta.arr_se) so the NZB/job
  matches. Non-arr schemes filter by parsed SxxEyy season / absolute ordering.
- **Secret transport** (fillarr-style):
  - Passwords leave the browser only as SHA-256 (crypto.subtle, with a verified pure-JS
    fallback for plain-http LANs where crypto.subtle is unavailable; fallback checked against
    hashlib on multiple vectors incl. UTF-8). Stored hashes carry a "sha2$" prefix; legacy
    hashes verify one final plaintext login and are transparently upgraded
    (auth.verify_login). /ui/auth/state exposes pw_scheme so the client knows what to send.
  - API keys and the proxy password are MASKED in every UI payload (settings: abcd…wxyz,
    instances/proxy: "********" sentinel). Saving a sentinel keeps the stored secret
    (instances matched by name — renaming an instance requires re-entering its key).
    The full Streamarr key is served only by POST /ui/apikey (copy button).
  - apikey/rotate responds with the masked key only.
- Suite grew to 89 cases incl. season expansion (mock arr with a 2-episode season),
  mask/sentinel round-trips, sha2/legacy login upgrade, and reveal endpoint.

## 3k. Round-11 (v1.1.0.0) — season completeness, security audit, release prep

- Season expansion: hard 5-search cap removed; every unmatched episode may trigger a
  second-chance search (config `ratelimit.season_search_max`, default 40), each batch is
  pooled so one search can satisfy several episodes; summary log line per season request.
- Security audit fixes: `downloader._fs_safe` sanitises job names/categories (traversal,
  separators, dot-files); addfile NZB capped at 1 MB, restore at 200 MB; security headers
  middleware (nosniff, X-Frame-Options DENY, no-referrer); session cookie `secure` behind
  HTTPS/X-Forwarded-Proto; config.yml chmod 0600 (holds api key/password hash/proxy creds);
  uvicorn access log disabled — arr apps put the apikey in query strings, which previously
  leaked into container logs (Streamarr's own request logging never includes it).
- SQLite: WAL + synchronous=NORMAL, cache_size 64 MB, temp_store MEMORY, mmap 128 MB.
- Release prep: version 1.1.0.0 (minor bump approved), .gitignore/.dockerignore/CHANGELOG.md,
  README badges + contributing/disclaimer, personal data stripped from docs.

## 3l. Round-12 (v1.1.1.0) — queue UX

- Queue and history are paginated (20 per page; history server-side via
  /ui/history?limit&offset returning {total, limit, offset, items} with limit capped at 100;
  queue client-side). db.jobs_history gained offset + jobs_history_count.
- Download ETA: downloader keeps a ~75 s rolling window of (ts, bytes_done) samples per job
  (_speed_hist); _avg_speed computes the last-minute average; queue_snapshot exposes speed
  (rolling average) and eta seconds; the SAB queue payload fills timeleft (H:MM:SS) from it.
- UI reworked toward the fillarr look: download cards (title, status/category badges,
  done/total, speed, ETA, slim full-width progress bar, inline priority/actions) replace the
  queue and history tables; responsive below 700 px; pager component.

## 3m. Round-13 (v1.1.2.0)

- **Configurable port**: streamarr.port editable in Settings (validated 1-65535); the
  entrypoint substitutes it into the uvicorn CMD at boot (env STREAMARR_PORT overrides);
  Docker port mapping must be adjusted by the user.
- **Queue/history back to tables**: sortable headers (name/category/priority/status/size/
  speed/ETA; manual reordering disabled while sorted), text filter per table, page size
  20/50/100/200/500 (persisted in localStorage; history server-pages capped at 100/page),
  checkbox multi-select with bulk pause/resume/delete.
- **Six priorities** (100 Force, 2 Highest, 1 Higher, 0 Normal, -1 Lower, -2 Lowest);
  worker orders by priority DESC. Per-instance `default_priority` applied to grabs by
  matching the addfile category to the app type (tv→sonarr, movies→radarr, music→lidarr,
  books/audiobooks→readarr, adult→whisparr); per-subscription `priority`.
- **Naming fixes from the Terra-X log**: clean() maps "/"→"-" ("(1/3)"→"(1-3)", previously
  "(13)"), and arr-named releases strip source-embedded SxxEyy fragments ("(S01/E02)") that
  could mislead the arr parser.
- **Subscriptions, Pinchflat-style**: POST /ui/subs/quick {url[, backlog]} guesses provider/
  preset (30+ domain map), title (last meaningful path segment, @/model/channel prefixes
  dropped), id slug, media (audio presets), and path <downloads>/<site>/<title>/; the first
  check starts immediately in the background. Per-sub `initial` = new_only (baseline,
  default) | backlog (first check downloads everything) — the log's 'bluecrow 0 new items'
  was the baseline working as designed; backlog mode is the way to fetch existing uploads.
  UI: URL quick-add hero with Backlog checkbox; blank ids/titles guessed on save everywhere
  (indexers, instances name from type, subscriptions).
- **Lidarr first-class**: newznab accepts artist/album/author/title params as the query
  (t=book added); addfile with cat music/audiobooks/podcasts forces media=audio (FFmpeg
  audio extraction to the configured format); "ytmusic" preset for music.youtube.com.
- **Statistics graphs**: minute-granularity aggregate speed samples land in the stats event
  log (event=speed, indexer 'total'); /ui/stats/timeseries?range=24h|7d|30d buckets grabs
  and average speed into 48 buckets; dashboard renders two SVG area charts with a range
  toggle.
- **19 new site presets** (37 total): music ytmusic/bandcamp/mixcloud/audiomack; adult
  redtube/spankbang/eporner/tnaflix; general twitch/dailymotion/rumble/bilibili/nicovideo/
  twitter/instagram/reddit/archiveorg/odysee/bitchute.

## 3n. Round-14 (v1.1.3.0)

- Explicit listen port in Settings → General (entrypoint applies at boot; restart required).
- Subscription path de-dup: `_downloads_root()` strips a trailing known-site folder so
  /downloads/youtube base still yields /downloads/pornhub/<name>.
- Per-subscription Save + Start now (POST /ui/subs/run → subscriptions.process_one).
- 403 fix (PornHub etc.): Chrome UA, more retries, yt-dlp browser impersonation when
  available (ytdlp.impersonate toggle); requirements pin yt-dlp[default,curl-cffi].

## 3o. Round-15 (v1.1.4.0) — final review

- **Security fix (critical)**: POST /ui/auth/setup had no auth dependency — with forms login
  enabled, an unauthenticated request could replace the credentials (account takeover). It
  now requires a valid session when a password_hash exists (in 'local' mode a local-network
  requester counts, consistent with all other /ui endpoints). Regression test uses a
  cookie-free TestClient.
- **UI bug**: the per-subscription Start-now button rendered without a label — its title
  attribute was missing the closing quote, so the browser swallowed ">Start now" into the
  attribute. Fixed + a source-level test guards the closing quote. Dead selector from the
  provider→_site rename removed.
- **Performance**: indexes idx_cache_indexer_pub(indexer_id, published DESC),
  idx_jobs_status(status, completed DESC), idx_jobs_active(status, priority DESC, position),
  idx_stats_ts(ts); GZipMiddleware (min 1 KB) for UI/API payloads. db connections were
  already thread-local.
- **Hygiene**: pyflakes-clean (dead imports/vars removed).
- **README**: rewritten for a GitHub audience — hero pitch, quick start, three-step guide,
  feature/config tables, FAQ; screenshot placeholders under docs/screenshots/ (dashboard,
  indexer-add, instances, queue, subscriptions).

## 4. Behavioural details & known limitations

- **Pause of an active download** = abort + status Paused (yt-dlp cannot suspend). Resume
  re-runs yt-dlp; partial fragments are typically continued. Documented in README FAQ.
- **Flat channel extraction has no upload dates** → `published` is NULL for channel items;
  ordering/identity comes from `ordinal`. Broad-search items get `published=now` so RSS sorts.
- **Ordinal stability**: ordinals are recomputed per refresh from the full reversed listing.
  If a channel deletes an old video, later ordinals shift — acceptable for the use case,
  noted here for future work (could pin ordinals once assigned).
- **yt-dlp version after update**: new version loads only after container restart
  (running process keeps the imported module).
- **Size in Newznab results** is an estimate from duration (250 kB/s video, 20 kB/s audio)
  since real size is unknown pre-download. Arr apps only use it for display/limits.
- **max_concurrent** exists in config but the worker is single-threaded (one download at a
  time — deliberate, matches rate-limit philosophy). Field reserved for future use.
- **Episode filter** (`season`/`ep` params) filters cached items; if nothing matches it
  returns the unfiltered list so the arr can decide (avoids false "no results" on sparse caches).
- **SQLite across threads**: thread-local connections; WAL mode. FastAPI runs sync endpoints
  in a threadpool — safe. Never share a cursor across threads.
- **Restore** replaces config.yml + db, then `config.load()`; a container restart is
  recommended (db handles per thread may still point at the old inode).
- **Security review**: outbound = configured+enabled arr instances, YouTube (only when a
  YouTube indexer enabled), mediathekviewweb.de (only when Mediathek indexer enabled),
  PyPI (only during yt-dlp update). No other calls. All state-changing endpoints require auth.
  Passwords pbkdf2 (200k iters); sessions itsdangerous-signed, httponly, samesite=lax;
  0.3 s login delay against brute force; API key compared with hmac.compare_digest.

## 5. Testing

`tests/` — 111 cases (incl. mock-arr auto-configure integration), all passing (`python3 -m pytest`). conftest boots the app via
`TestClient` with a temp `STREAMARR_CONFIG_DIR`. Covered: config roundtrip + URL
normalization, naming schemes + parsing, auth (setup-once, wrong password, API key header,
401 without key), Newznab caps/search/episode-filter/NZB payload, SABnzbd version/get_config/
addfile→queue/foreign-NZB rejection/reorder/speedlimit/history, UI search/grab/indexer
validation/settings/backup/stats/logs. Live checks performed during development: full server
boot, first-run setup, pseudo-NZB roundtrip via curl multipart, reorder, failure path into
history with verbose error log, metrics output.

## 6. Extending

**New provider** (e.g. adult sites for Whisparr — the agreed next goal):
1. `providers/<site>.py` with a search/list function returning cache-item dicts
   (`id` = `<provider>:<unique>`, plus `indexer_id/provider/series_title/title/url/published/duration/ordinal/meta`).
2. Register in `indexers.search()` and the provider choices in `api/ui.py:save_indexers`
   + the provider `<select>` in `static/app.js` (pages.indexers).
3. yt-dlp already downloads most adult sites' URLs directly — often no downloader change needed.
4. Category 6000 (XXX) is already named in `indexers._cat_name`.

**New settings section**: add to `config.DEFAULTS`, `ui.SETTING_SECTIONS`, and a panel in
`pages.settings` (app.js).

**Version bumps**: edit `streamarr/__init__.py` only; keep `1.0.` prefix.

## 7. Build & run

```bash
docker build -t streamarr:latest .
docker compose up -d          # or the docker run from README
# dev, without docker:
STREAMARR_CONFIG_DIR=/tmp/cfg STREAMARR_DOWNLOADS_DIR=/tmp/dl \
  python3 -m uvicorn streamarr.main:app --port 8585
python3 -m pytest             # from repo root or tests/
```
