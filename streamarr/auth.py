import hashlib
import hmac
import ipaddress
import secrets

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import config

SESSION_COOKIE = "streamarr_session"
SESSION_MAX_AGE = 14 * 86400


def _serializer():
    return URLSafeTimedSerializer(config.get()["streamarr"]["auth"]["session_secret"])


PW_SCHEME_PREFIX = "sha2$"  # password_hash over a client-side SHA-256, never plaintext


def pw_scheme():
    h = config.get()["streamarr"]["auth"].get("password_hash", "")
    return "sha2" if h.startswith(PW_SCHEME_PREFIX) else "legacy"


def store_hash(client_value):
    """client_value is the SHA-256 the browser sends — plaintext never crosses the wire."""
    return PW_SCHEME_PREFIX + hash_password(client_value)


def verify_login(received, stored):
    """Returns (ok, upgraded_hash_or_None). Legacy hashes verify against plaintext and are
    transparently upgraded to the sha2 scheme on success."""
    import hashlib as _hl
    if stored.startswith(PW_SCHEME_PREFIX):
        return verify_password(received, stored[len(PW_SCHEME_PREFIX):]), None
    if verify_password(received, stored):  # legacy: received is the plaintext
        digest = _hl.sha256(received.encode()).hexdigest()
        return True, store_hash(digest)
    return False, None


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"{salt}${digest}"


def verify_password(password, stored):
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt).split("$", 1)[1], digest)


def mode():
    return config.get()["streamarr"]["auth"].get("mode", "none")


def needs_setup():
    a = config.get()["streamarr"]["auth"]
    return a.get("mode") == "forms" and not (a["username"] and a["password_hash"])


def is_local(request: Request):
    try:
        ip = ipaddress.ip_address(request.client.host)
        return ip.is_private or ip.is_loopback
    except (ValueError, AttributeError):
        return False


def create_session():
    return _serializer().dumps({"u": config.get()["streamarr"]["auth"]["username"]})


def check_session(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    try:
        _serializer().loads(token, max_age=SESSION_MAX_AGE)
        return True
    except BadSignature:
        return False


def check_api_key(request: Request):
    key = (request.query_params.get("apikey")
           or request.headers.get("X-Api-Key") or "")
    return hmac.compare_digest(key, config.get()["streamarr"]["api_key"])


def require_ui(request: Request):
    """Dependency for UI JSON endpoints — honours the auth mode."""
    m = mode()
    if m == "none":
        return True
    if m == "local" and is_local(request):
        return True
    if check_session(request) or check_api_key(request):
        return True
    raise HTTPException(status_code=401, detail="Authentication required")


def require_api_key(request: Request):
    """Dependency for machine endpoints (Newznab/SAB): API key only."""
    if check_api_key(request):
        return True
    raise HTTPException(status_code=401, detail="Invalid or missing API key")
