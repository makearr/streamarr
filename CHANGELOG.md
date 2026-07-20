# Changelog

Versioning: `MAJOR.MINOR.PATCH.HOTFIX` — the first two numbers only change for deliberate milestones.

## 1.1.4.0
- Security: closed an account-takeover hole — /ui/auth/setup now requires a valid session when an account already exists
- Fixed the unlabeled Start-now button on the Subscriptions page (missing quote swallowed the label)
- Performance: SQLite indexes on the hot query paths, gzip compression for UI/API payloads
- Code hygiene: pyflakes-clean source
- README rewritten for GitHub with screenshot placeholders (docs/screenshots/)

## 1.1.3.0
- Explicit listen-port field in Settings
- Fixed subscription auto-path doubling the site folder (/downloads/pornhub/<name>)
- Per-subscription Save and Start-now buttons
- Fixed HTTP 403 download failures (PornHub and others) via yt-dlp browser impersonation + Chrome UA; ships curl_cffi

## 1.1.2.0
- Configurable port (Settings → General; entrypoint applies it at boot)
- Queue/history: sortable + filterable tables, multi-select bulk actions, page size 20-500
- Six priorities (Force…Lowest) with per-upstream-app and per-subscription defaults
- URL-only subscription quick-add (auto provider/title/path, optional backlog) — Pinchflat-style
- Lidarr first-class: artist/album search params, music grabs extracted to audio, YouTube Music preset
- Dashboard statistics graphs (grabs & speed, 24h/7d/30d)
- 19 new site presets; release-name fixes for embedded numbering
- Fixed: titles with "/" no longer collapse ("(1/3)" stays readable as "(1-3)")

## 1.1.1.0
- Paginated queue and history (20 per page)
- Per-download ETA from the last minute's average speed (also served as SABnzbd `timeleft`)
- Download-card UI for queue and history

## 1.1.0.0
- Season requests: no hard cap on per-episode second-chance searches (pooled re-matching, configurable `ratelimit.season_search_max`, default 40)
- Security audit: filesystem-safe job names/categories, upload size limits, security headers (nosniff/DENY/no-referrer), secure session cookies behind HTTPS, `config.yml` written with mode 0600, uvicorn access log disabled (API keys no longer appear in container logs)
- SQLite tuning: WAL + `synchronous=NORMAL`, 64 MB page cache, in-memory temp store, 128 MB mmap
- Release housekeeping: .gitignore/.dockerignore/CHANGELOG, sanitized docs

## 1.0.x highlights
- 1.0.9: whole-season requests expand into per-episode releases; client-side SHA-256 password transport with legacy upgrade; masked secrets in all UI payloads
- 1.0.8: Mediathek/ARD/ZDF arr-test fix (empty-query handling), subcategory attrs
- 1.0.7: 480p bug fixed via sort-based yt-dlp format selection; chosen format logged
- 1.0.6: structurally valid pseudo-NZBs (arr validation), series-aware matching, format fallback for direct URLs
- 1.0.5: second-chance episode-title search, empty feed on no-match, persisted arr episode identity
- 1.0.4: arr title-based episode resolution, exact YouTube upload dates, verbose request logging, first-class subscriptions with per-subscription path/interval
- 1.0.3: SABnzbd urlBase fix (/api alias), subscriptions with arr cross-check, copy-log button
- 1.0.2: per-indexer quality, public-URL guessing with arr-validated candidates, proxy bypass, yt-dlp auto-update with idle restart, Readarr support, queue priorities
- 1.0.1: optional login (open by default, local-network mode), preset-based indexer setup, ARD/ZDF direct providers, 15+ streaming-site presets, dashboard
- 1.0.0: initial release — Newznab indexer + SABnzbd-compatible download client for YouTube and German Mediathek
