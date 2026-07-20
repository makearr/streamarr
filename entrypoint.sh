#!/bin/sh
set -e
# linuxserver.io-compatible environment handling
PUID=${PUID:-1000}
PGID=${PGID:-1000}
UMASK=${UMASK:-022}
TZ=${TZ:-Etc/UTC}

ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime 2>/dev/null || true
echo "$TZ" > /etc/timezone 2>/dev/null || true
umask "$UMASK"

if [ "$(id -u)" = "0" ]; then
    groupmod -o -g "$PGID" streamarr >/dev/null 2>&1 || true
    usermod -o -u "$PUID" streamarr >/dev/null 2>&1 || true
    mkdir -p /config /downloads 2>/dev/null || true
    # best-effort: bind mounts (NFS root-squash, unprivileged LXC) may forbid chown —
    # fine as long as PUID/PGID already own the data
    chown streamarr:streamarr /config /downloads 2>/dev/null \
        || echo "chown /config skipped (not permitted) — assuming UID=$PUID already owns it"
    chown -R streamarr:streamarr /app/.local 2>/dev/null || true
    echo "Streamarr starting as UID=$PUID GID=$PGID TZ=$TZ"
    exec gosu streamarr "$@"
fi
exec "$@"
