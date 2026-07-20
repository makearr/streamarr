import logging
import time

from . import arr, config, db, downloader, naming
from .providers import site, youtube

log = logging.getLogger("streamarr.subscriptions")

_CAT = {5000: "tv", 2000: "movies", 3000: "music", 3010: "music",
        3030: "audiobooks", 7000: "books", 7020: "books", 6000: "adult"}


def category_for(idx):
    return _CAT.get((idx.get("categories") or [5000])[0], "")


def _pseudo_idx(sub):
    """Indexer-shaped context so providers/quality helpers work for standalone subs."""
    return {
        "id": f"sub_{sub['id']}", "name": sub["title"], "provider": sub["provider"],
        "site_preset": sub.get("site_preset", ""), "media": sub.get("media", "video"),
        "naming": sub.get("naming", "date"), "categories": [5000],
        "quality": sub.get("quality") or {}, "search_template": "",
    }


def _list(idx, sub):
    if idx["provider"] == "youtube":
        return youtube.list_channel(sub["url"], sub["title"], idx["id"])
    return site.list_source(sub["url"], sub["title"], idx)


def _arr_already_has(title):
    for inst in config.get()["instances"]:
        if not inst.get("enabled") or inst["type"] not in ("sonarr", "whisparr"):
            continue
        try:
            if arr.has_release(inst, title):
                return inst["name"]
        except Exception as exc:
            log.debug("Arr cross-check against %s failed: %s", inst["name"], exc)
    return None


def due_subs(force=False):
    """First-class subs whose individual interval has elapsed, plus legacy indexer subs."""
    now = time.time()
    out = []
    for sub in config.get()["subs"]:
        if not sub.get("enabled") or not sub.get("url"):
            continue
        key = f"sub_{sub['id']}"
        if force or now - db.subs_last_run(key) >= sub["interval_minutes"] * 60:
            out.append((_pseudo_idx(sub), sub, sub.get("path") or None,
                        sub.get("category") or "", key))
    # legacy: subscriptions attached to indexers keep working
    for idx in config.get()["indexers"]:
        if not idx.get("enabled"):
            continue
        for s in idx.get("subscriptions") or []:
            key = f"idx_{idx['id']}_{s['title']}"
            interval = config.get()["subscriptions"]["interval_minutes"]
            if force or now - db.subs_last_run(key) >= interval * 60:
                out.append((idx, dict(s, id=key), None, category_for(idx), key))
    return out


def process(force=False):
    """Check due subscriptions; enqueue new items. Returns number of enqueued downloads."""
    cfg = config.get()
    if not cfg["subscriptions"].get("enabled") and not force:
        return 0
    enqueued = 0
    for idx, sub, outdir, category, run_key in due_subs(force):
        try:
            enqueued += _process_sub(idx, sub, outdir, category)
        except Exception as exc:
            log.warning("Subscription '%s' failed: %s", sub.get("title"), exc)
        db.subs_set_run(run_key)
    return enqueued


def process_one(sub_id, force=True):
    """Run a single first-class subscription by id (Start now button)."""
    sub = next((x for x in config.get()["subs"] if x["id"] == sub_id), None)
    if not sub or not sub.get("url"):
        return 0
    key = f"sub_{sub['id']}"
    try:
        n = _process_sub(_pseudo_idx(sub), sub, sub.get("path") or None, sub.get("category") or "")
    except Exception as exc:
        log.warning("Subscription '%s' failed: %s", sub.get("title"), exc)
        n = 0
    db.subs_set_run(key)
    return n


def _process_sub(idx, sub, outdir, category):
    log.info("Subscription check: '%s' (%s)", sub["title"], sub["url"])
    items = _list(idx, sub)
    if not items:
        log.info("Subscription '%s': source returned no items", sub["title"])
        return 0
    db.cache_upsert(items)
    known = db.subs_known(idx["id"], sub["title"])
    new = [it for it in items if it["id"] not in known]
    if db.subs_is_new_source(idx["id"], sub["title"]):
        if sub.get("initial") == "backlog":
            log.info("Subscription '%s': backlog mode — downloading all %d existing items",
                     sub["title"], len(items))
            new = items
        else:
            for it in items:
                db.subs_mark(it["id"], idx["id"], sub["title"], downloaded=False)
            log.info("Subscription '%s': baseline set (%d existing items; enable 'Download "
                     "backlog' to fetch them) — new uploads will download from now on",
                     sub["title"], len(items))
            return 0
    count = 0
    tag = naming.quality_tag(config.quality_for(idx), idx["media"])
    override = sub.get("check_arr", "")
    check = {"on": True, "off": False}.get(override, config.get()["subscriptions"].get("check_arr"))
    for it in new:
        if it.get("provider") == "youtube":
            it = youtube.ensure_exact_date(it)
        title = naming.release_title(it, idx.get("naming", "date"), tag)
        holder = _arr_already_has(title) if check else None
        if holder:
            log.info("Subscription '%s': '%s' already present in %s — skipped",
                     sub["title"], title, holder)
            db.subs_mark(it["id"], idx["id"], sub["title"], downloaded=False)
            continue
        downloader.enqueue(name=title, url=it["url"], category=category,
                           indexer_id=idx["id"], provider=it["provider"], media=idx["media"],
                           priority=int(sub.get("priority") or 0), outdir=outdir)
        db.subs_mark(it["id"], idx["id"], sub["title"], downloaded=True)
        count += 1
    log.info("Subscription '%s': %d new item(s) queued", sub["title"], count)
    return count
