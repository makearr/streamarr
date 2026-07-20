import copy
import os
import re
import secrets
import threading

import yaml

CONFIG_DIR = os.environ.get("STREAMARR_CONFIG_DIR", "/config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.yml")

DEFAULTS = {
    "streamarr": {
        "port": 8585,
        "base_url": "",
        "public_url": "",        # URL under which other services reach Streamarr; guessed if empty
        "api_key": "",          # generated on first start
        "log_level": "INFO",
        "auth": {"mode": "none", "username": "", "password_hash": "", "session_secret": ""},  # mode: none|local|forms
    },
    "downloads": {
        "path": os.environ.get("STREAMARR_DOWNLOADS_DIR", "/downloads"),
        "speed_limit_kbps": 0,   # 0 = unlimited
        "max_concurrent": 1,
        "paused": False,
    },
    "quality": {
        "max_resolution": 1080,
        "max_fps": 60,
        "video_format": "mp4",
        "audio_codec": "aac",
        "audio_format": "m4a",
    },
    "sponsorblock": {"enabled": False, "categories": ["sponsor"]},
    "ratelimit": {
        "sleep_requests": 1,
        "download_delay": 3,
        "rate_limit_sleep": 300,
        "exponential_backoff": True,
        "backoff_multiplier": 2.0,
        "backoff_max": 3600,
        "season_search_max": 40,   # extra per-episode searches allowed per season request
    },
    "cache": {"retention_days": 30, "refresh_minutes": 60},
    "ytdlp": {"auto_update": True, "update_interval_hours": 24, "restart_after_update": True, "impersonate": True},
    "proxy": {
        "enabled": False,
        "type": "http",          # http | https | socks4 | socks5
        "host": "",
        "port": 8080,
        "username": "",
        "password": "",
        "bypass_local": True,
        "ignored_addresses": "",  # comma separated, wildcards allowed (arr-style)
    },
    "subscriptions": {"enabled": True, "interval_minutes": 60, "check_arr": True},
    "subs": [],                  # first-class subscriptions, see SUB_DEFAULTS
    "indexers": [],
    "instances": [],
}

INDEXER_DEFAULTS = {
    "id": "",
    "name": "",
    "enabled": True,
    "provider": "youtube",       # youtube | mediathek
    "media": "video",            # video | audio
    "naming": "absolute",        # absolute | date | auto (mediathek: parse SxxEyy, fallback date)
    "categories": [5000],
    "broad_search": False,       # youtube: allow arbitrary ytsearch beyond channel list
    "channels": [],              # [{title, url}] — for site providers: any yt-dlp listable URL
    "site_preset": "",           # providers/site.py preset id (provider == "site")
    "search_template": "",       # override search URL, {query} placeholder
    "quality": {},               # per-indexer overrides of the global quality section
    "subscriptions": [],         # [{title, url}] — new releases are downloaded automatically
}

SUB_DEFAULTS = {
    "id": "",
    "title": "",
    "url": "",
    "provider": "youtube",       # youtube | site
    "site_preset": "",
    "media": "video",
    "naming": "date",            # absolute | date | auto | sxxeyy
    "category": "",
    "path": "",                  # custom storage path; empty = downloads.path/category
    "interval_minutes": 60,
    "check_arr": "",             # "" = global default | "on" | "off"
    "priority": 0,               # -2 lowest … 2 highest, 100 force
    "initial": "new_only",       # new_only | backlog (download existing items on first check)
    "enabled": True,
}

INSTANCE_DEFAULTS = {
    "default_priority": 0,       # applied to grabs pushed by this app (matched by category)
    "name": "",
    "type": "sonarr",            # sonarr | radarr | lidarr | whisparr | prowlarr
    "url": "",
    "api_key": "",
    "verify_ssl": False,     # local instances rarely have valid certs
    "enabled": True,
    "auto_configure": False,     # push indexer + download client on save/startup
    "own_url": "",               # URL under which this instance reaches Streamarr
    "indexer_ids": [],           # empty = all enabled indexers
}

_lock = threading.RLock()
_config = None


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def normalize_url(url, default_scheme="http"):
    """Accept 'host:port', 'host', 'http://…', 'https://…'; return scheme://host[:port] without trailing slash."""
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = f"{default_scheme}://{url}"
    return url


def load():
    global _config
    with _lock:
        raw = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE) as f:
                raw = yaml.safe_load(f) or {}
        cfg = _merge(DEFAULTS, raw)
        cfg["indexers"] = [_merge(INDEXER_DEFAULTS, i) for i in cfg.get("indexers") or []]
        cfg["instances"] = [_merge(INSTANCE_DEFAULTS, i) for i in cfg.get("instances") or []]
        cfg["subs"] = [_merge(SUB_DEFAULTS, s) for s in cfg.get("subs") or []]
        changed = False
        if not cfg["streamarr"]["api_key"]:
            cfg["streamarr"]["api_key"] = secrets.token_hex(16)
            changed = True
        if not cfg["streamarr"]["auth"]["session_secret"]:
            cfg["streamarr"]["auth"]["session_secret"] = secrets.token_hex(32)
            changed = True
        _config = cfg
        if changed:
            save()
        return _config


def save():
    with _lock:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w") as f:
            yaml.safe_dump(_config, f, sort_keys=False, allow_unicode=True)
        os.replace(tmp, CONFIG_FILE)
        try:
            os.chmod(CONFIG_FILE, 0o600)  # api key, password hash, proxy credentials
        except OSError:
            pass


def get():
    with _lock:
        if _config is None:
            return load()
        return _config


def update(section, values):
    with _lock:
        cfg = get()
        if isinstance(values, list):
            cfg[section] = values
        else:
            cfg[section] = _merge(cfg.get(section, {}), values)
        save()
        return cfg[section]


def proxy_url():
    p = get()["proxy"]
    if not p.get("enabled") or not p.get("host"):
        return None
    auth = ""
    if p.get("username"):
        auth = p["username"]
        if p.get("password"):
            auth += f":{p['password']}"
        auth += "@"
    return f"{p['type']}://{auth}{p['host']}:{p['port']}"


def proxy_for(target_url):
    """Proxy URL for a target, honouring local bypass and arr-style ignored addresses."""
    import fnmatch
    import ipaddress
    from urllib.parse import urlparse
    proxy = proxy_url()
    if not proxy or not target_url:
        return proxy
    host = urlparse(target_url if "://" in target_url else f"http://{target_url}").hostname or ""
    p = get()["proxy"]
    if p.get("bypass_local"):
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback:
                return None
        except ValueError:
            if "." not in host or host == "localhost":  # bare docker/LAN hostnames
                return None
    for pat in (p.get("ignored_addresses") or "").split(","):
        pat = pat.strip()
        if pat and fnmatch.fnmatch(host, pat):
            return None
    return proxy


def quality_for(idx):
    """Global quality defaults overridden by the indexer's own quality settings."""
    q = dict(get()["quality"])
    for k, v in (idx.get("quality") or {}).items():
        if v not in ("", None):
            q[k] = v
    return q


def public_url_candidates(own_url=""):
    """Ordered guesses for the URL under which other containers reach Streamarr."""
    import socket
    cfg = get()["streamarr"]
    port = cfg["port"]
    cands = []
    for u in (own_url, cfg.get("public_url")):
        if u:
            cands.append(normalize_url(u))
    cands.append(f"http://streamarr:{port}")            # docker-compose DNS default
    try:
        host = socket.gethostname()
        cands.append(f"http://{host}:{port}")
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))
        cands.append(f"http://{s.getsockname()[0]}:{port}")  # container IP — last resort
        s.close()
    except OSError:
        pass
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out
