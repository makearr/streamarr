import collections
import logging
import threading
import time

# --- in-memory log buffer for the UI log viewer ---

LOG_BUFFER = collections.deque(maxlen=2000)
_seq = 0


class BufferHandler(logging.Handler):
    def emit(self, record):
        global _seq
        _seq += 1
        LOG_BUFFER.append({
            "seq": _seq,
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
        })


def setup_logging(level="INFO"):
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter("%(message)s")
    bh = BufferHandler()
    bh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.handlers = [bh, sh]


def log_connection_error(log, what, url, exc):
    """Verbose connection diagnostics: name the target, phase and root cause."""
    chain, cause, seen = [], exc, set()
    while cause and id(cause) not in seen and len(chain) < 6:
        seen.add(id(cause))
        chain.append(f"{type(cause).__name__}: {cause}")
        cause = cause.__cause__ or cause.__context__
    log.error("Connection to %s failed (url=%s). Cause chain: %s", what, url, " <- ".join(chain))


# --- status bar ---

_status = {"text": "Idle", "busy": False, "since": time.time()}
_status_lock = threading.Lock()


def set_status(text, busy=True):
    with _status_lock:
        _status.update({"text": text, "busy": busy, "since": time.time()})


def clear_status():
    set_status("Idle", busy=False)


def get_status():
    with _status_lock:
        return dict(_status)


# --- provider rate limiter with exponential backoff ---

class RateLimiter:
    """Per-provider request spacing plus exponential backoff after 429/overload."""

    def __init__(self, cfg_getter):
        self._cfg = cfg_getter
        self._lock = threading.Lock()
        self._last_request = {}
        self._backoff_until = {}
        self._backoff_current = {}

    def wait(self, provider):
        cfg = self._cfg()
        with self._lock:
            until = self._backoff_until.get(provider, 0)
            last = self._last_request.get(provider, 0)
        now = time.time()
        if until > now:
            remaining = int(until - now)
            logging.getLogger("streamarr.ratelimit").warning(
                "%s is backing off for another %ss", provider, remaining)
            time.sleep(until - now)
        sleep = cfg["sleep_requests"] - (time.time() - last)
        if sleep > 0:
            time.sleep(sleep)
        with self._lock:
            self._last_request[provider] = time.time()

    def penalize(self, provider):
        cfg = self._cfg()
        with self._lock:
            current = self._backoff_current.get(provider, 0)
            if cfg["exponential_backoff"] and current:
                current = min(current * cfg["backoff_multiplier"], cfg["backoff_max"])
            else:
                current = cfg["rate_limit_sleep"]
            self._backoff_current[provider] = current
            self._backoff_until[provider] = time.time() + current
        logging.getLogger("streamarr.ratelimit").warning(
            "%s reported overload/rate limit — backing off %ss", provider, int(current))
        return current

    def reset(self, provider):
        with self._lock:
            self._backoff_current.pop(provider, None)
            self._backoff_until.pop(provider, None)

    def state(self):
        now = time.time()
        with self._lock:
            return {p: int(u - now) for p, u in self._backoff_until.items() if u > now}


from . import config  # noqa: E402

limiter = RateLimiter(lambda: config.get()["ratelimit"])
