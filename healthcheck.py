import sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:8585/ping", timeout=5) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
