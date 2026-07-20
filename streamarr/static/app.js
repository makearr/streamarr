"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = t => String(t ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtMB = b => b >= 1073741824 ? (b/1073741824).toFixed(2)+" GB" : (b/1048576).toFixed(1)+" MB";
const fmtSpeed = b => b ? (b/1048576).toFixed(2)+" MB/s" : "";
const fmtEta = sec => {
  if (sec == null || !isFinite(sec)) return "—";
  if (sec < 60) return sec + "s";
  if (sec < 3600) return Math.floor(sec/60) + "m " + (sec%60) + "s";
  return Math.floor(sec/3600) + "h " + Math.floor(sec%3600/60) + "m";
};
const pager = (id, page, total, per) => {
  const pages = Math.max(1, Math.ceil(total / per));
  return `<div class="pager" data-pager="${id}">
    <button class="btn icon" data-pg="${page-1}" ${page<=0?"disabled":""}>‹</button>
    <span>${page+1} / ${pages}</span>
    <button class="btn icon" data-pg="${page+1}" ${page>=pages-1?"disabled":""}>›</button>
    <span class="dim">${total} item${total===1?"":"s"}</span></div>`;
};
const RES_OPTS = [[2160,"2160p (4K)"],[1440,"1440p"],[1080,"1080p"],[720,"720p"],[480,"480p"]];
const FPS_OPTS = [[60,"60 fps"],[30,"30 fps"]];
const VFMT_OPTS = ["mp4","mkv","webm"];
const ACODEC_OPTS = ["aac","opus","mp3"];
const AFMT_OPTS = ["m4a","mp3","opus","m4b"];
const PRIO = [[100,"Force"],[2,"Highest"],[1,"Higher"],[0,"Normal"],[-1,"Lower"],[-2,"Lowest"]];
function makeSortable(table, rows, renderRow, tbody) {
  $$("th[data-sort]", table).forEach(th => th.onclick = () => {
    const k = th.dataset.sort, dir = th.dataset.dir === "asc" ? -1 : 1;
    th.dataset.dir = dir === 1 ? "asc" : "desc";
    rows.sort((a, b) => (a[k] > b[k] ? 1 : a[k] < b[k] ? -1 : 0) * dir);
    tbody.innerHTML = rows.map(renderRow).join("");
    tbody.dispatchEvent(new Event("rewire"));
  });
}
const PROVIDER_LABEL = p => ({youtube:"YouTube", mediathek:"Mediathek", ard:"ARD", zdf:"ZDF", site:"Site", manual:"Manual"}[p] || p);

async function api(path, opts = {}) {
  if (opts.json) { opts.body = JSON.stringify(opts.json); opts.headers = {"Content-Type": "application/json"}; opts.method = opts.method || "POST"; }
  const r = await fetch("/ui" + path, opts);
  if (r.status === 401) { showAuth(); throw new Error("unauthorized"); }
  if (!r.ok) { let d = ""; try { d = (await r.json()).detail; } catch {} throw new Error(d || r.statusText); }
  return r.json();
}
const toastErr = e => { const b = $("#banner"); b.style.background = ""; b.textContent = e.message || e; b.classList.remove("hidden"); setTimeout(() => b.classList.add("hidden"), 6000); };
const toastOk = m => { const b = $("#banner"); b.style.background = "var(--ok)"; b.textContent = m; b.classList.remove("hidden"); setTimeout(() => { b.classList.add("hidden"); b.style.background = ""; }, 2500); };

/* ---------- crypto: passwords leave the browser only as SHA-256 ---------- */
function sha256Fallback(bytes) {  // bytes: Uint8Array
  const K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  let H = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
  const rr = (v, c) => (v >>> c) | (v << (32 - c));
  const l = bytes.length, bitLen = l * 8;
  const padded = new Uint8Array((((l + 8) >> 6) + 1) << 6);
  padded.set(bytes); padded[l] = 0x80;
  const dv = new DataView(padded.buffer);
  dv.setUint32(padded.length - 4, bitLen >>> 0);
  dv.setUint32(padded.length - 8, Math.floor(bitLen / 0x100000000));
  const w = new Int32Array(64);
  for (let off = 0; off < padded.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getInt32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rr(w[i-15],7) ^ rr(w[i-15],18) ^ (w[i-15] >>> 3);
      const s1 = rr(w[i-2],17) ^ rr(w[i-2],19) ^ (w[i-2] >>> 10);
      w[i] = (w[i-16] + s0 + w[i-7] + s1) | 0;
    }
    let [a,b,c,d,e,f,g,h] = H;
    for (let i = 0; i < 64; i++) {
      const S1 = rr(e,6) ^ rr(e,11) ^ rr(e,25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + K[i] + w[i]) | 0;
      const S0 = rr(a,2) ^ rr(a,13) ^ rr(a,22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    H = [ (H[0]+a)|0, (H[1]+b)|0, (H[2]+c)|0, (H[3]+d)|0,
          (H[4]+e)|0, (H[5]+f)|0, (H[6]+g)|0, (H[7]+h)|0 ];
  }
  return H.map(x => (x >>> 0).toString(16).padStart(8, "0")).join("");
}
async function sha256hex(text) {
  if (crypto.subtle) {
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
  }
  return sha256Fallback(new TextEncoder().encode(text));  // plain-http LAN fallback
}

/* ---------- theme ---------- */
function applyTheme() {
  const pref = localStorage.getItem("theme") || "auto";
  const dark = pref === "dark" || (pref === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  $$(".theme-switch button").forEach(b => b.classList.toggle("active", b.dataset.theme === pref));
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
document.addEventListener("click", e => {
  const b = e.target.closest(".theme-switch button");
  if (b) { localStorage.setItem("theme", b.dataset.theme); applyTheme(); }
});

/* ---------- auth ---------- */
let authState = {mode: "none"};
function showAuth() { $("#auth-overlay").classList.remove("hidden"); $("#app").classList.add("hidden"); }
function showApp() { $("#auth-overlay").classList.add("hidden"); $("#app").classList.remove("hidden"); route(); }
async function initAuth() {
  authState = await fetch("/ui/auth/state").then(r => r.json());
  if (authState.mode === "none" || (authState.mode === "local" && authState.local) || authState.authenticated) return showApp();
  showAuth();
}
$("#auth-form").addEventListener("submit", async e => {
  e.preventDefault();
  const err = $("#auth-error"); err.classList.add("hidden");
  try {
    const pw = authState.pw_scheme === "legacy"
      ? $("#auth-pass").value                        // one last plaintext login upgrades the hash
      : await sha256hex($("#auth-pass").value);
    await api("/auth/login", {json: {username: $("#auth-user").value, password: pw}});
    authState.pw_scheme = "sha2";
    showApp();
  } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
});
$("#logout").addEventListener("click", async () => {
  await api("/auth/logout", {method: "POST"}).catch(() => {});
  authState.mode === "forms" ? showAuth() : toastOk("Login is disabled — enable it under Settings → Security");
});

/* ---------- status bar + backend banner ---------- */
async function pollStatus() {
  try {
    const s = await api("/status");
    $("#status-text").textContent = s.status.text;
    $("#status-dot").classList.toggle("busy", s.status.busy);
    $("#status-version").textContent = "v" + s.version + " · yt-dlp " + s.ytdlp_version;
    const bo = Object.entries(s.backoff || {});
    $("#status-backoff").classList.toggle("hidden", !bo.length);
    if (bo.length) $("#status-backoff").textContent = bo.map(([p, t]) => `${p}: backing off ${t}s`).join(" · ");
    const sp = $("#status-speed");
    sp.classList.toggle("hidden", !s.speed_limit_kbps);
    if (s.speed_limit_kbps) sp.textContent = `limit ${s.speed_limit_kbps} KB/s`;
  } catch {}
}
async function pollHealth() {
  try {
    const h = await api("/health/instances");
    const down = h.filter(i => i.ok === false);
    const b = $("#banner");
    if (down.length) { b.style.background = ""; b.textContent = "Backend unavailable: " + down.map(i => `${i.name} — ${i.detail}`).join(" | "); b.classList.remove("hidden"); }
    else if (b.textContent.startsWith("Backend unavailable")) b.classList.add("hidden");
  } catch {}
}

/* ---------- routing ---------- */
const pages = {};
let queueTimer = 0, logTimer = 0;
function route() {
  clearInterval(queueTimer); clearInterval(logTimer);
  const page = (location.hash || "#dashboard").slice(1).split("/")[0];
  $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.page === page));
  (pages[page] || pages.dashboard)();
}
addEventListener("hashchange", route);

/* ---------- dashboard (landing) ---------- */
pages.dashboard = async () => {
  const d = await api("/dashboard").catch(() => null);
  if (!d) return;
  const idxSel = d.indexers.filter(i => i.enabled);
  const totals = {search: 0, grab: 0, download_ok: 0, download_fail: 0};
  d.stats.forEach(r => totals[r.event] = (totals[r.event] || 0) + r.count);
  $("#page").innerHTML = `
    <div class="dash-hero">
      <h2>Streamarr</h2>
      <p>Search your streaming indexers or paste any supported URL to download it directly.</p>
      <div class="dash-search">
        <select id="d-indexer">${idxSel.map(i => `<option value="${esc(i.id)}">${esc(i.name)}</option>`).join("") || "<option value=''>no indexers</option>"}</select>
        <input id="d-q" placeholder="Search — or paste a video/audio URL…">
        <button class="btn accent" id="d-go">Go</button>
      </div>
    </div>
    <div class="tiles">
      <div class="tile"><div class="num">${d.queue_total}</div><div class="lbl">In queue</div></div>
      <div class="tile"><div class="num">${totals.grab || 0}</div><div class="lbl">Grabs</div></div>
      <div class="tile"><div class="num">${totals.download_ok || 0}</div><div class="lbl">Completed</div></div>
      <div class="tile"><div class="num">${totals.download_fail || 0}</div><div class="lbl">Failed</div></div>
    </div>
    <div class="dash-grid">
      <div class="dash-card"><h3>Active downloads <a href="#queue">Queue →</a></h3><div id="d-queue"></div></div>
      <div class="dash-card"><h3>Arr instances <a href="#instances">Manage →</a></h3><div id="d-inst"><span class="dim">Checking…</span></div></div>
      <div class="dash-card"><h3>Streaming services <a href="#indexers">Manage →</a></h3>
        <div class="chiprow">${d.indexers.map(i => `<span class="chip"><span class="dot ${i.enabled ? "on" : "off"}"></span>${esc(i.name)} <span class="dim">${PROVIDER_LABEL(i.provider)}${i.site_preset ? "/" + esc(i.site_preset) : ""}</span></span>`).join("") || `<span class="dim">No indexers configured yet.</span>`}</div></div>
      <div class="dash-card wide"><h3>Statistics <span class="range-toggle" id="stat-range">
          <a data-r="24h" class="on">24h</a><a data-r="7d">7d</a><a data-r="30d">30d</a></span></h3>
        <div id="stat-graphs" class="dim">Loading…</div></div>
      <div class="dash-card"><h3>Recent history <a href="#queue">Queue →</a></h3>
        ${d.history.slice(0, 6).map(j => `<div class="dash-row"><span class="pill ${j.status === "Completed" ? "ok" : "err"}">${j.status === "Completed" ? "✔" : "✖"}</span><span class="name">${esc(j.name)}</span></div>`).join("") || `<span class="dim">Nothing yet.</span>`}</div>
    </div>`;
  const renderQ = () => api("/queue").then(q => {
    $("#d-queue").innerHTML = q.jobs.slice(0, 6).map(j => {
      const pct = j.bytes_total ? Math.floor(j.bytes_done / j.bytes_total * 100) : 0;
      return `<div class="dash-row"><span class="name">${esc(j.name)}</span>
        <div class="progress" style="min-width:90px"><div style="width:${pct}%"></div></div>
        <span class="pct">${j.status === "Downloading" ? pct + "%" : j.status}</span></div>`;
    }).join("") || `<span class="dim">Queue is empty${q.paused ? " (paused)" : ""}.</span>`;
  }).catch(() => {});
  renderQ(); queueTimer = setInterval(renderQ, 3000);
  api("/health/instances").then(h => {
    $("#d-inst").innerHTML = h.map(i => `<div class="dash-row"><span class="dot ${i.ok ? "on" : i.ok === null ? "off" : ""}" style="${i.ok === false ? "background:var(--danger)" : ""}"></span><span class="name">${esc(i.name)}</span><span class="dim">${esc(i.ok === null ? "disabled" : i.detail)}</span></div>`).join("") || `<span class="dim">No instances configured.</span>`;
  }).catch(() => {});
  const go = () => {
    const v = $("#d-q").value.trim();
    if (/^https?:\/\//i.test(v)) {
      api("/download", {json: {url: v}}).then(() => { toastOk("Download queued"); location.hash = "#queue"; }).catch(toastErr);
    } else {
      sessionStorage.setItem("searchq", v); sessionStorage.setItem("searchidx", $("#d-indexer").value);
      location.hash = "#search";
    }
  };
  $("#d-go").onclick = go;
  $("#d-q").addEventListener("keydown", e => e.key === "Enter" && go());
  renderStatGraphs("24h");
  $$("#stat-range a").forEach(a => a.onclick = () => {
    $$("#stat-range a").forEach(x => x.classList.remove("on"));
    a.classList.add("on");
    renderStatGraphs(a.dataset.r);
  });
};

/* ---------- queue ---------- */
pages.queue = () => {
  let per = +(localStorage.getItem("q-per") || 20);
  let qPage = 0, hPage = 0, qFilter = "", hFilter = "", sortK = null, sortDir = 1;
  const selected = new Set();
  $("#page").innerHTML = `<h1>Queue</h1>
    <div class="toolbar">
      <button class="btn" id="q-toggle">Pause queue</button>
      <label class="check">Speed limit <input id="q-limit" type="number" min="0" style="width:90px"> KB/s</label>
      <button class="btn" id="q-limit-apply">Apply</button>
      <input id="q-filter" placeholder="Filter…" style="width:160px">
      <select id="q-per">${[20,50,100,200,500].map(n => `<option ${n===per?"selected":""}>${n}</option>`).join("")}</select>
      <span class="spacer"></span>
      <button class="btn" id="q-sel-pause" disabled>⏸ Selected</button>
      <button class="btn" id="q-sel-resume" disabled>▶ Selected</button>
      <button class="btn danger" id="q-sel-delete" disabled>✕ Selected</button>
      <button class="btn accent" id="q-add">Add download (URL)</button>
    </div>
    <div id="q-addform" class="panel hidden">
      <div class="form-grid">
        <label class="field"><span>URL (any yt-dlp-supported site)</span><input id="qa-url" placeholder="https://…"></label>
        <label class="field"><span>Name (optional)</span><input id="qa-name"></label>
        <label class="field"><span>Category</span><select id="qa-cat"><option value="">none</option><option>tv</option><option>movies</option><option>music</option><option>books</option><option>audiobooks</option><option>podcasts</option><option>adult</option></select></label>
        <label class="field"><span>Media</span><select id="qa-media"><option value="video">Video</option><option value="audio">Audio only</option></select></label>
      </div>
      <button class="btn accent" id="qa-go">Download</button>
    </div>
    <div id="q-table"></div>
    <h1 style="margin-top:22px">History</h1>
    <div class="toolbar"><input id="h-filter" placeholder="Filter history…" style="width:200px"></div>
    <div id="q-history"></div>`;
  $("#q-add").onclick = () => $("#q-addform").classList.toggle("hidden");
  $("#qa-go").onclick = async () => {
    try {
      await api("/download", {json: {url: $("#qa-url").value, name: $("#qa-name").value, category: $("#qa-cat").value, media: $("#qa-media").value}});
      $("#q-addform").classList.add("hidden"); $("#qa-url").value = ""; toastOk("Download queued");
    } catch (e) { toastErr(e); }
  };
  $("#q-toggle").onclick = async () => {
    const paused = $("#q-toggle").dataset.paused === "1";
    await api("/queue/" + (paused ? "resume" : "pause"), {json: {}}); renderQueue();
  };
  $("#q-limit-apply").onclick = () => api("/queue/speedlimit", {json: {kbps: +$("#q-limit").value || 0}}).then(() => toastOk("Speed limit applied")).catch(toastErr);
  $("#q-per").onchange = () => { per = +$("#q-per").value; localStorage.setItem("q-per", per); qPage = hPage = 0; renderQueue(); renderHist(); };
  $("#q-filter").oninput = () => { qFilter = $("#q-filter").value.toLowerCase(); qPage = 0; renderQueue(); };
  $("#h-filter").oninput = () => { hFilter = $("#h-filter").value.toLowerCase(); hPage = 0; renderHist(); };

  const bulk = async act => {
    for (const id of selected) await api("/queue/" + act, {json: {nzo_id: id}}).catch(() => {});
    selected.clear(); renderQueue();
  };
  $("#q-sel-pause").onclick = () => bulk("pause");
  $("#q-sel-resume").onclick = () => bulk("resume");
  $("#q-sel-delete").onclick = () => bulk("delete");
  const updateBulk = () => ["q-sel-pause","q-sel-resume","q-sel-delete"].forEach(id => $("#"+id).disabled = !selected.size);

  const row = (j, i, total, manual) => {
    const pct = j.bytes_total ? Math.floor(j.bytes_done / j.bytes_total * 100) : 0;
    return `<tr>
      <td><input type="checkbox" data-sel="${j.nzo_id}" ${selected.has(j.nzo_id)?"checked":""}></td>
      <td><button class="btn icon" data-act="up" data-id="${j.nzo_id}" data-pos="${i-1}" ${!manual||i===0?"disabled":""}>▲</button><button class="btn icon" data-act="down" data-id="${j.nzo_id}" data-pos="${i+1}" ${!manual||i===total-1?"disabled":""}>▼</button></td>
      <td class="td-name" title="${esc(j.name)}">${esc(j.name)}</td><td>${esc(j.category || "—")}</td>
      <td><select data-prio="${j.nzo_id}">${PRIO.map(([v,l]) => `<option value="${v}" ${j.priority===v?"selected":""}>${l}</option>`).join("")}</select></td>
      <td><span class="pill ${j.status === "Downloading" ? "ok" : ""}">${j.status}</span></td>
      <td><div style="display:flex;align-items:center"><div class="progress"><div style="width:${pct}%"></div></div><span class="pct">${pct}%</span></div></td>
      <td>${j.bytes_total ? fmtMB(j.bytes_total) : "—"}</td><td>${fmtSpeed(j.speed)}</td>
      <td title="Estimate from the average speed of the last minute">${j.status === "Downloading" ? fmtEta(j.eta) : "—"}</td>
      <td>${j.status === "Paused"
          ? `<button class="btn icon" data-act="resume" data-id="${j.nzo_id}">▶</button>`
          : `<button class="btn icon" data-act="pause" data-id="${j.nzo_id}">⏸</button>`}
        <button class="btn icon danger" data-act="delete" data-id="${j.nzo_id}">✕</button></td></tr>`;
  };

  const renderQueue = async () => {
    const d = await api("/queue").catch(() => null);
    if (!d) return;
    $("#q-toggle").textContent = d.paused ? "Resume queue" : "Pause queue";
    $("#q-toggle").dataset.paused = d.paused ? "1" : "0";
    let jobs = d.jobs;
    if (qFilter) jobs = jobs.filter(j => (j.name + " " + (j.category||"") + " " + j.status).toLowerCase().includes(qFilter));
    if (!jobs.length) { $("#q-table").innerHTML = `<div class="panel empty">${qFilter ? "No queue entries match the filter." : "The queue is empty. Grabs from your arr apps, manual grabs and URL downloads appear here."}</div>`; return; }
    const manual = !sortK;
    if (sortK) jobs = [...jobs].sort((a, b) => ((a[sortK] ?? "") > (b[sortK] ?? "") ? 1 : -1) * sortDir);
    const pages = Math.max(1, Math.ceil(jobs.length / per));
    if (qPage >= pages) qPage = pages - 1;
    const slice = jobs.slice(qPage * per, qPage * per + per);
    $("#q-table").innerHTML = `<table><thead><tr><th></th><th title="Manual order (disabled while sorted)"></th>
      <th data-sort="name">Name ↕</th><th data-sort="category">Category ↕</th><th data-sort="priority">Priority ↕</th>
      <th data-sort="status">Status ↕</th><th>Progress</th><th data-sort="bytes_total">Size ↕</th>
      <th data-sort="speed">Speed ↕</th><th data-sort="eta">ETA ↕</th><th></th></tr></thead>
      <tbody>${slice.map((j, i) => row(j, qPage * per + i, jobs.length, manual)).join("")}</tbody></table>`
      + (jobs.length > per ? pager("q", qPage, jobs.length, per) : "");
    $$('#q-table [data-pager="q"] [data-pg]').forEach(b => b.onclick = () => { qPage = +b.dataset.pg; renderQueue(); });
    $$("#q-table th[data-sort]").forEach(th => th.onclick = () => { sortK = th.dataset.sort; sortDir = -sortDir; renderQueue(); });
    $$("#q-table [data-sel]").forEach(cb => cb.onchange = () => { cb.checked ? selected.add(cb.dataset.sel) : selected.delete(cb.dataset.sel); updateBulk(); });
    $$("#q-table [data-act]").forEach(b => b.onclick = async () => {
      const act = b.dataset.act;
      if (act === "up" || act === "down") await api("/queue/move", {json: {nzo_id: b.dataset.id, position: +b.dataset.pos}});
      else await api("/queue/" + act, {json: {nzo_id: b.dataset.id}});
      renderQueue();
    });
    $$("#q-table [data-prio]").forEach(sel => sel.onchange = () =>
      api("/queue/priority", {json: {nzo_id: sel.dataset.prio, priority: +sel.value}}).then(renderQueue).catch(toastErr));
    updateBulk();
  };

  let hSortK = null, hSortDir = 1;
  const renderHist = async () => {
    const h = await api(`/history?limit=${Math.min(per,100)}&offset=${hPage * Math.min(per,100)}`).catch(() => null);
    if (!h) return;
    if (!h.total) { $("#q-history").innerHTML = `<div class="panel empty">Nothing finished yet.</div>`; return; }
    let items = h.items;
    if (hFilter) items = items.filter(j => (j.name + " " + j.status).toLowerCase().includes(hFilter));
    if (hSortK) items = [...items].sort((a, b) => ((a[hSortK] ?? "") > (b[hSortK] ?? "") ? 1 : -1) * hSortDir);
    $("#q-history").innerHTML = `<table><thead><tr>
      <th data-hsort="name">Name ↕</th><th data-hsort="status">Status ↕</th>
      <th data-hsort="bytes_total">Size ↕</th><th data-hsort="completed">Finished ↕</th></tr></thead><tbody>` +
      items.map(j => `<tr><td class="td-name" title="${esc(j.fail_message || j.name)}">${esc(j.name)}</td>
        <td><span class="pill ${j.status === "Completed" ? "ok" : "err"}">${j.status}</span></td>
        <td>${j.bytes_total ? fmtMB(j.bytes_total) : "—"}</td>
        <td>${j.completed ? new Date(j.completed * 1000).toLocaleString() : ""}</td></tr>`).join("") +
      "</tbody></table>" + pager("h", hPage, h.total, Math.min(per,100));
    $$('#q-history [data-pager="h"] [data-pg]').forEach(b => b.onclick = () => { hPage = +b.dataset.pg; renderHist(); });
    $$("#q-history th[data-hsort]").forEach(th => th.onclick = () => { hSortK = th.dataset.hsort; hSortDir = -hSortDir; renderHist(); });
  };

  renderQueue(); renderHist();
  queueTimer = setInterval(() => { renderQueue(); renderHist(); }, 2500);
};

/* ---------- statistics graphs ---------- */
function svgLine(values, w, h, color, fill) {
  const max = Math.max(...values, 1);
  const pts = values.map((v, i) => `${(i / (values.length - 1) * w).toFixed(1)},${(h - v / max * (h - 4)).toFixed(1)}`);
  const line = `<polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2"/>`;
  const area = fill ? `<polygon points="0,${h} ${pts.join(" ")} ${w},${h}" fill="${color}" opacity="0.12"/>` : "";
  return area + line;
}
async function renderStatGraphs(range) {
  const d = await api(`/stats/timeseries?range=${range}`).catch(() => null);
  if (!d) { $("#stat-graphs").textContent = "No data yet."; return; }
  const W = 560, H = 90;
  const totalGrabs = d.grabs.reduce((a, b) => a + b, 0);
  const maxSpeed = Math.max(...d.speed);
  $("#stat-graphs").innerHTML = `
    <div class="graph-label">Grabs — ${totalGrabs} in ${range}</div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="graph">${svgLine(d.grabs, W, H, "var(--accent)", true)}</svg>
    <div class="graph-label">Download speed — peak ${fmtSpeed(maxSpeed) || "0 MB/s"}</div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="graph">${svgLine(d.speed, W, H, "#7aa2ff", true)}</svg>`;
}

/* ---------- history (merged into queue) ---------- */
pages.history = () => { location.hash = "#queue"; };

/* ---------- subscriptions ---------- */
pages.subscriptions = async () => {
  let [subs, presets, settings] = await Promise.all([
    api("/subs").catch(() => []),
    api("/presets").catch(() => ({sites: {}})),
    api("/settings").catch(() => null)]);
  const render = () => {
    $("#page").innerHTML = `<h1>Subscriptions</h1>
      <div class="panel">
        <div class="dash-search">
          <input id="sub-quick-url" placeholder="Paste a channel / playlist / model URL — everything else is guessed…">
          <label class="check" title="Also download all existing items, not just new uploads"><input type="checkbox" id="sub-quick-backlog"> Backlog</label>
          <button class="btn accent" id="sub-quick-go">Subscribe</button>
        </div>
        <div class="hint">e.g. https://www.youtube.com/@SomeChannel → saved to &lt;downloads&gt;/youtube/SomeChannel/, checked hourly. Fine-tune below after adding.</div>
      </div>
      <div class="toolbar">
        <button class="btn" id="sub-add">＋ Add manually</button>
        <button class="btn" id="sub-save">Save all</button>
        <button class="btn" id="sub-run">Check all now</button>
        <span id="sub-out" class="dim"></span>
      </div>
      <div class="hint">Subscribed channels/playlists are checked on their own interval; new releases are downloaded automatically.
      The first check only sets a baseline — the existing backlog is not downloaded. Any yt-dlp-supported site works — the same presets as on the <a href="#indexers">Indexers page</a>; check there for source URL formats and hints, or configure a site as an indexer first to try it out.</div>
      <div id="sub-list"></div>`;
    $("#sub-list").innerHTML = subs.map((x, n) => `<div class="panel" data-n="${n}">
      <div class="form-grid">
        <label class="field"><span>ID (a-z 0-9 _ -)</span><input data-f="id" value="${esc(x.id)}"></label>
        <label class="field"><span>Title (series/folder name in releases)</span><input data-f="title" value="${esc(x.title)}"></label>
        <label class="field"><span>Channel / playlist URL</span><input data-f="url" value="${esc(x.url)}" placeholder="https://www.youtube.com/@channel"></label>
        <label class="field"><span>Site</span><select data-f="_site">
          <option value="youtube" ${x.provider==="youtube"?"selected":""}>YouTube</option>
          ${Object.entries(presets.sites).map(([sid, sp]) => `<option value="site::${sid}" ${x.provider==="site"&&x.site_preset===sid?"selected":""}>${esc(sp.name)}</option>`).join("")}</select></label>
        <label class="field"><span>Skip if already in an arr instance</span><select data-f="check_arr">
          <option value="" ${!x.check_arr?"selected":""}>Global default (Settings → Subscriptions)</option>
          <option value="on" ${x.check_arr==="on"?"selected":""}>On — cross-check and skip</option>
          <option value="off" ${x.check_arr==="off"?"selected":""}>Off — always download</option></select></label>
        <label class="field"><span>Media</span><select data-f="media">
          <option value="video" ${x.media==="video"?"selected":""}>Video</option>
          <option value="audio" ${x.media==="audio"?"selected":""}>Audio only</option></select></label>
        <label class="field"><span>Naming</span><select data-f="naming">
          ${[["date","Date-based"],["absolute","Absolute (upload order)"],["auto","Auto (SxxEyy → date)"],["sxxeyy","SxxEyy strict"]].map(([v,l]) => `<option value="${v}" ${x.naming===v?"selected":""}>${l}</option>`).join("")}</select></label>
        <label class="field"><span>Storage path (empty = ${esc(settings ? settings.downloads.path : "/downloads")}/&lt;category&gt;)</span><input data-f="path" value="${esc(x.path || "")}" placeholder="/downloads/youtube/MyChannel"></label>
        <label class="field"><span>Category (for the default path and arr imports)</span><select data-f="category">
          ${["","tv","movies","music","books","audiobooks","podcasts","adult"].map(c => `<option value="${c}" ${x.category===c?"selected":""}>${c || "none"}</option>`).join("")}</select></label>
        <label class="field"><span>Check interval (minutes)</span><input data-f="interval_minutes" type="number" min="5" value="${esc(x.interval_minutes)}"></label>
        <label class="field"><span>Priority for queued downloads</span><select data-f="priority">
          ${PRIO.map(([v,l]) => `<option value="${v}" ${(+x.priority||0)===v?"selected":""}>${l}</option>`).join("")}</select></label>
        <label class="field"><span>First check</span><select data-f="initial">
          <option value="new_only" ${x.initial!=="backlog"?"selected":""}>Only new uploads from now on</option>
          <option value="backlog" ${x.initial==="backlog"?"selected":""}>Download the whole backlog</option></select></label>
      </div>
      <label class="check"><input type="checkbox" data-f="enabled" ${x.enabled?"checked":""}> Enabled</label>
      <div class="sub-actions">
        <button class="btn accent" data-save-one="${n}">Save</button>
        <button class="btn" data-run-one="${n}" ${x.id?"":"disabled"} title="${x.id?"Check this subscription now":"Save first"}">Start now</button>
        <span class="dim" data-out="${n}"></span>
        <span class="spacer"></span>
        <button class="btn danger" data-del="${n}">Delete subscription</button>
      </div>
    </div>`).join("") || `<div class="panel empty">No subscriptions yet — add a channel or playlist to auto-download new releases.</div>`;
    $("#sub-quick-go").onclick = async () => {
      const url = $("#sub-quick-url").value.trim();
      if (!url) return;
      try {
        const created = await api("/subs/quick", {json: {url, backlog: $("#sub-quick-backlog").checked}});
        toastOk(`Subscribed '${created.title}' → ${created.path} (first check running)`);
        subs = await api("/subs"); render();
      } catch (e) { toastErr(e); }
    };
    $("#sub-add").onclick = () => { subs.push({id: "", title: "", url: "", provider: "youtube", site_preset: "", media: "video", naming: "date", category: "", path: "", interval_minutes: 60, priority: 0, initial: "new_only", enabled: true}); render(); };
    $$("#sub-list [data-del]").forEach(b => b.onclick = () => { subs.splice(+b.dataset.del, 1); render(); });
    const fillGuessed = x => {
      if (!x.title && x.url) x.title = (x.url.split("/").filter(Boolean).pop() || "").replace(/^@/, "").slice(0, 60);
      if (!x.id) x.id = slug(x.title) || "sub-" + Math.random().toString(36).slice(2, 8);
    };
    $$("#sub-list [data-save-one]").forEach(b => b.onclick = async () => {
      collect();
      subs.forEach(fillGuessed);
      try { subs = await api("/subs", {json: subs}); render(); toastOk("Subscription saved"); }
      catch (e) { toastErr(e); }
    });
    $$("#sub-list [data-run-one]").forEach(b => b.onclick = async () => {
      const x = subs[+b.dataset.runOne];
      const out = $(`[data-out="${b.dataset.runOne}"]`);
      out.textContent = "Running…";
      try { const r = await api("/subs/run", {json: {id: x.id}}); out.textContent = r.detail; }
      catch (e) { out.textContent = e.message; }
    });
    $("#sub-save").onclick = async () => {
      collect();
      subs.forEach(fillGuessed);
      try { subs = await api("/subs", {json: subs}); render(); toastOk("Subscriptions saved"); } catch (e) { toastErr(e); }
    };
    $("#sub-run").onclick = async () => {
      $("#sub-out").textContent = "Running…";
      const r = await api("/subscriptions/run", {method: "POST"}).catch(e => ({detail: e.message}));
      $("#sub-out").textContent = r.detail;
    };
  };
  const slug = t => (t || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 32);
  const collect = () => $$("#sub-list .panel[data-n]").forEach(p => {
    const x = subs[+p.dataset.n];
    $$("[data-f]", p).forEach(el => {
      const f = el.dataset.f;
      if (f === "priority") { x[f] = +el.value; return; }
      if (f === "_site") {
        const v = el.value;
        if (v === "youtube") { x.provider = "youtube"; x.site_preset = ""; }
        else { x.provider = "site"; x.site_preset = v.slice(6); }
      }
      else if (el.type === "checkbox") x[f] = el.checked;
      else if (el.type === "number") x[f] = +el.value;
      else x[f] = el.value.trim();
    });
  });
  render();
};

/* ---------- manual search ---------- */
pages.search = async () => {
  const idxs = await api("/indexers").catch(() => []);
  $("#page").innerHTML = `<h1>Manual search</h1>
    <div class="toolbar">
      <select id="s-indexer">${idxs.filter(i => i.enabled).map(i => `<option value="${esc(i.id)}">${esc(i.name)}</option>`).join("")}</select>
      <input id="s-q" placeholder="Search term" style="min-width:240px">
      <input id="s-season" type="number" min="1" placeholder="S" title="Season (optional)" style="width:64px">
      <input id="s-ep" type="number" min="1" placeholder="E" title="Episode (optional)" style="width:64px">
      <button class="btn accent" id="s-go">Search</button>
    </div>
    <div class="hint">Season/episode filter matches SxxEyy in titles, or the upload-order number for absolute-numbered indexers.</div>
    <div id="s-results"></div>`;
  const pre = sessionStorage.getItem("searchq");
  if (pre !== null) { $("#s-q").value = pre; sessionStorage.removeItem("searchq"); }
  const preIdx = sessionStorage.getItem("searchidx");
  if (preIdx) { $("#s-indexer").value = preIdx; sessionStorage.removeItem("searchidx"); }
  const go = async () => {
    $("#s-results").innerHTML = `<div class="panel empty">Searching…</div>`;
    try {
      let url = `/search?indexer_id=${encodeURIComponent($("#s-indexer").value)}&q=${encodeURIComponent($("#s-q").value)}`;
      if (+$("#s-season").value && +$("#s-ep").value) url += `&season=${+$("#s-season").value}&ep=${+$("#s-ep").value}`;
      const items = await api(url);
      if (!items.length) { $("#s-results").innerHTML = `<div class="panel empty">No results. Source-list indexers may still be warming their cache.</div>`; return; }
      $("#s-results").innerHTML = `<table><thead><tr><th>Release</th><th>Series / channel</th><th>Duration</th><th></th></tr></thead><tbody>` +
        items.map(it => `<tr><td>${esc(it.release_title)}</td><td>${esc(it.series_title || "")}</td>
          <td>${it.duration ? Math.round(it.duration / 60) + " min" : "—"}</td>
          <td><button class="btn accent" data-grab="${esc(it.id)}">Grab</button></td></tr>`).join("") + "</tbody></table>";
      $$("#s-results [data-grab]").forEach(b => b.onclick = async () => {
        b.disabled = true; b.textContent = "Queued";
        await api("/grab", {json: {indexer_id: $("#s-indexer").value, item_id: b.dataset.grab}}).catch(toastErr);
      });
    } catch (e) { $("#s-results").innerHTML = ""; toastErr(e); }
  };
  $("#s-go").onclick = go;
  $("#s-q").addEventListener("keydown", e => e.key === "Enter" && go());
  if (pre) go();
};

/* ---------- indexers ---------- */
pages.indexers = async () => {
  let [idxs, presets, settings] = await Promise.all([
    api("/indexers").catch(() => []),
    api("/presets").catch(() => ({providers: {}, sites: {}, categories: [], url_candidates: []})),
    api("/settings").catch(() => null)]);
  const baseUrl = (settings && (settings.streamarr.public_url || settings.url_guess)) || location.origin;
  const NAMING = [["absolute", "Absolute (upload order → S01Exxx)"], ["sxxeyy", "SxxEyy from title (strict — items without a tag are hidden)"],
    ["auto", "Auto (SxxEyy from title → date fallback)"], ["date", "Date-based (YYYY-MM-DD)"],
    ["arr", "From upstream arr request (season/episode taken from the search)"]];
  const QSEL = (x, key, opts, label) => `<label class="field"><span>${label}</span>
    <select data-q="${key}"><option value="">Global default</option>
    ${opts.map(o => { const [v, l] = Array.isArray(o) ? o : [o, o];
      return `<option value="${v}" ${(x.quality||{})[key] == v ? "selected" : ""}>${l}</option>`; }).join("")}</select></label>`;
  const render = () => {
    $("#page").innerHTML = `<h1>Indexers</h1>
      <div class="toolbar"><button class="btn accent" id="i-add">＋ Add indexer</button>
      <button class="btn" id="i-save">Save all</button></div>
      <div id="i-picker" class="panel hidden">
        <b>Pick a source</b>
        <div class="preset-grid" id="i-presetgrid"></div>
      </div>
      <div id="i-list"></div>`;
    const cards = [];
    for (const [pid, p] of Object.entries(presets.providers)) {
      if (pid === "site") continue;
      cards.push({provider: pid, preset: "", name: p.name, hint: p.hint});
    }
    for (const [sid, sp] of Object.entries(presets.sites)) cards.push({provider: "site", preset: sid, name: sp.name, hint: sp.hint});
    $("#i-presetgrid").innerHTML = cards.map((c, n) => `<div class="preset-card" data-pick="${n}"><b>${esc(c.name)}</b><small>${esc(c.hint)}</small></div>`).join("");
    $$("#i-presetgrid [data-pick]").forEach(el => el.onclick = () => {
      const c = cards[+el.dataset.pick];
      const sp = c.preset ? presets.sites[c.preset] : null;
      const q = {};
      if (sp && sp.audio_format) q.audio_format = sp.audio_format;
      idxs.push({id: "", name: c.name, provider: c.provider, site_preset: c.preset, search_template: "",
        media: (sp && sp.media) || "video", naming: c.provider === "youtube" ? "arr" : c.provider === "site" ? "absolute" : "auto",
        categories: (sp && sp.categories) || (["mediathek","ard","zdf"].includes(c.provider) ? [5000, 2000] : [5000]),
        enabled: true, broad_search: false, channels: [], quality: q});
      $("#i-picker").classList.add("hidden"); render();
    });
    $("#i-add").onclick = () => $("#i-picker").classList.toggle("hidden");
    $("#i-list").innerHTML = idxs.map((x, n) => {
      const sp = x.provider === "site" ? (presets.sites[x.site_preset] || presets.sites.custom) : null;
      const tip = x.id ? `<div class="hint" style="user-select:all">Connection for this indexer — Newznab URL: <code>${esc(baseUrl)}/newznab/${esc(x.id)}</code> · API path: <code>/api</code> · Categories: <code>${esc((x.categories||[]).join(","))}</code> · Download client (SABnzbd): host <code>${esc(baseUrl.replace(/^https?:\/\//, "").split(":")[0])}</code> port <code>${esc(baseUrl.split(":")[2] || "8585")}</code> · API key: Settings → Security</div>` : "";
      return `<div class="panel" data-n="${n}">
      <b>${esc(x.name || "New indexer")}</b> <span class="dim">— ${PROVIDER_LABEL(x.provider)}${x.site_preset ? " / " + esc(x.site_preset) : ""}</span>
      ${sp ? `<div class="hint">${esc(sp.hint)}</div>` : ""}${tip}
      <div class="form-grid">
        <label class="field"><span>Indexer ID / name — the URL slug used in <code>/newznab/&lt;id&gt;/api</code> (a-z 0-9 _ -)</span><input data-f="id" value="${esc(x.id)}"></label>
        <label class="field"><span>Display name (shown in the UI and in upstream arrs)</span><input data-f="name" value="${esc(x.name)}"></label>
        <label class="field"><span>Media</span><select data-f="media">
          <option value="video" ${x.media==="video"?"selected":""}>Video</option>
          <option value="audio" ${x.media==="audio"?"selected":""}>Audio only</option></select></label>
        <label class="field"><span>Episode naming</span><select data-f="naming">
          ${NAMING.map(([v, l]) => `<option value="${v}" ${x.naming===v?"selected":""}>${l}</option>`).join("")}</select></label>
        ${x.provider === "site" ? `<label class="field"><span>Search URL template (optional, {query} placeholder)</span><input data-f="search_template" value="${esc(x.search_template || "")}" placeholder="${esc((sp && sp.search_template) || "no search — source list only")}"></label>` : ""}
      </div>
      <label class="field" style="max-width:100%"><span>Newznab categories</span>
        <span class="chiprow">${presets.categories.map(([v, l]) => `<label class="chip"><input type="checkbox" data-cat="${v}" ${(x.categories||[]).includes(v) ? "checked" : ""}> ${l} <span class="dim">${v}</span></label>`).join("")}</span></label>
      <details><summary class="hint" style="cursor:pointer">Quality for this indexer (empty = global defaults from Settings)</summary>
        <div class="form-grid">
          ${QSEL(x, "max_resolution", RES_OPTS, "Max resolution")}
          ${QSEL(x, "max_fps", FPS_OPTS, "Max FPS")}
          ${QSEL(x, "video_format", VFMT_OPTS, "Video container")}
          ${QSEL(x, "audio_codec", ACODEC_OPTS, "Audio codec")}
          ${QSEL(x, "audio_format", AFMT_OPTS, "Audio-only format")}
        </div></details>
      <label class="check"><input type="checkbox" data-f="enabled" ${x.enabled?"checked":""}> Enabled</label>
      ${x.provider === "youtube" ? `<label class="check"><input type="checkbox" data-f="broad_search" ${x.broad_search?"checked":""}> Allow broad YouTube search (arbitrary queries beyond the channel list)</label>` : ""}
      ${x.provider === "youtube" || x.provider === "site" ? `
        <label class="field" style="max-width:100%"><span>Sources — one per line: <i>Title | URL</i> (channels, shows, playlists, profiles)</span>
          <textarea data-f="channels" rows="4">${esc((x.channels||[]).map(c => c.title + " | " + c.url).join("\n"))}</textarea></label>
        <label class="field" style="max-width:100%"><span>Subscriptions — one per line: <i>Title | URL</i>. New releases are downloaded automatically (existing backlog is skipped on the first check)</span>
          <textarea data-f="subscriptions" rows="3">${esc((x.subscriptions||[]).map(c => c.title + " | " + c.url).join("\n"))}</textarea></label>` : ""}
      <button class="btn danger" data-del="${n}">Delete indexer</button>
    </div>`;
    }).join("") || `<div class="panel empty">No indexers yet — click "Add indexer" and pick a source.</div>`;
    $$("#i-list [data-del]").forEach(b => b.onclick = () => { idxs.splice(+b.dataset.del, 1); render(); });
    $("#i-save").onclick = async () => {
      collect();
      const slugI = t => (t || "").toLowerCase().replace(/[^a-z0-9]+/g, "").slice(0, 24);
      idxs.forEach(x => {
        if (!x.name) x.name = x.site_preset || x.provider;
        if (!x.id) {
          let base = slugI(x.name) || x.provider, id = base, n = 2;
          while (idxs.some(y => y !== x && y.id === id)) id = base + n++;
          x.id = id;
        }
      });
      try { idxs = await api("/indexers", {json: idxs}); render(); toastOk("Indexers saved"); } catch (e) { toastErr(e); }
    };
  };
  const collect = () => $$("#i-list .panel[data-n]").forEach(p => {
    const x = idxs[+p.dataset.n];
    x.categories = $$("[data-cat]", p).filter(el => el.checked).map(el => +el.dataset.cat);
    x.quality = {};
    $$("[data-q]", p).forEach(el => { if (el.value !== "") x.quality[el.dataset.q] = isNaN(+el.value) ? el.value : +el.value; });
    $$("[data-f]", p).forEach(el => {
      const f = el.dataset.f;
      if (f === "channels" || f === "subscriptions") x[f] = el.value.split("\n").map(l => l.split("|")).filter(a => a.length === 2).map(([t, u]) => ({title: t.trim(), url: u.trim()}));
      else if (el.type === "checkbox") x[f] = el.checked;
      else x[f] = el.value.trim();
    });
  });
  render();
};

/* ---------- instances ---------- */
pages.instances = async () => {
  let insts = await api("/instances").catch(() => []);
  const idxs = await api("/indexers").catch(() => []);
  const render = () => {
    $("#page").innerHTML = `<h1>Arr instances</h1>
      <div class="toolbar"><button class="btn accent" id="a-add">＋ Add instance</button>
      <button class="btn" id="a-save">Save all</button></div>
      <div class="hint">Saving triggers auto-configuration for every instance with “Configure automatically” enabled.</div>
      <div id="a-list"></div>`;
    $("#a-list").innerHTML = insts.map((x, n) => `<div class="panel" data-n="${n}">
      <div class="form-grid">
        <label class="field"><span>Name</span><input data-f="name" value="${esc(x.name)}"></label>
        <label class="field"><span>Type</span><select data-f="type">
          ${["sonarr","radarr","lidarr","readarr","whisparr","prowlarr"].map(t => `<option ${x.type===t?"selected":""}>${t}</option>`).join("")}</select></label>
        <label class="field"><span>URL (host:port or http(s)://…)</span><input data-f="url" value="${esc(x.url)}"></label>
        <label class="field"><span>API key</span><input data-f="api_key" value="${esc(x.api_key)}"></label>
        <label class="field"><span>Streamarr URL as seen by this instance</span><input data-f="own_url" value="${esc(x.own_url || "")}" placeholder="http://streamarr:8585"></label>
        <label class="field"><span>Indexers to push (comma-separated IDs, empty = all enabled)</span><input data-f="indexer_ids" value="${esc((x.indexer_ids||[]).join(","))}"></label>
        <label class="field"><span>Default priority for grabs from this app</span><select data-f="default_priority">
          ${PRIO.map(([v,l]) => `<option value="${v}" ${(+x.default_priority||0)===v?"selected":""}>${l}</option>`).join("")}</select></label>
      </div>
      <label class="check"><input type="checkbox" data-f="enabled" ${x.enabled?"checked":""}> Enabled</label>
      <label class="check"><input type="checkbox" data-f="verify_ssl" ${x.verify_ssl?"checked":""}> Validate HTTPS certificate</label>
      <label class="check"><input type="checkbox" data-f="auto_configure" ${x.auto_configure?"checked":""}> Configure automatically (on save and on every Streamarr start)</label>
      <div class="toolbar">
        <button class="btn" data-test="${n}">Test connection</button>
        <select data-cfgidx="${n}">${idxs.map(i => `<option value="${esc(i.id)}">${esc(i.name)}</option>`).join("")}</select>
        <button class="btn accent" data-cfg="${n}">Configure now</button>
        <button class="btn danger" data-del="${n}">Delete</button>
        <span data-result="${n}" class="dim"></span>
      </div></div>`).join("") || `<div class="panel empty">No arr instances configured.</div>`;
    $("#a-add").onclick = () => { insts.push({name: "", type: "sonarr", url: "", api_key: "", verify_ssl: false, enabled: true, auto_configure: false, own_url: "", indexer_ids: [], default_priority: 0}); render(); };
    $("#a-save").onclick = async () => { collect();
      insts.forEach(x => { if (!x.name) {
        const same = insts.filter(y => y !== x && (y.name || "").startsWith(x.type)).length;
        x.name = x.type + (same ? same + 1 : "");
      }});
      try { insts = await api("/instances", {json: insts}); render(); toastOk("Instances saved — auto-configuration running in the background"); } catch (e) { toastErr(e); } };
    $$("#a-list [data-del]").forEach(b => b.onclick = () => { insts.splice(+b.dataset.del, 1); render(); });
    $$("#a-list [data-test]").forEach(b => b.onclick = async () => {
      collect(); const n = +b.dataset.test, out = $(`[data-result="${n}"]`);
      out.textContent = "Testing…"; out.style.color = "";
      const r = await api("/instances/test", {json: insts[n]}).catch(e => ({ok: false, detail: e.message}));
      out.textContent = (r.ok ? "✔ " : "✖ ") + r.detail;
      out.style.color = r.ok ? "var(--ok)" : "var(--danger)";
    });
    $$("#a-list [data-cfg]").forEach(b => b.onclick = async () => {
      collect(); const n = +b.dataset.cfg, out = $(`[data-result="${n}"]`);
      await api("/instances", {json: insts});
      out.textContent = "Configuring… (trying URL candidates)"; out.style.color = "";
      try {
        let r = await api("/instances/configure", {json: {name: insts[n].name, indexer_id: $(`[data-cfgidx="${n}"]`).value, own_url: insts[n].own_url || ""}});
        if (!r.every(x => x.ok) && r.some(x => (x.detail || "").includes("HTTP 400"))) {
          const own = prompt("No guessed URL was reachable from this arr instance.\nURL where it can reach Streamarr:", `http://${location.hostname}:8585`);
          if (own) r = await api("/instances/configure", {json: {name: insts[n].name, indexer_id: $(`[data-cfgidx="${n}"]`).value, own_url: own}});
        }
        out.textContent = r.map(x => `${x.item}: ${x.ok ? x.detail : "FAILED — " + x.detail}`).join(" · ")
          + (r[0] && r[0].used_url ? ` (via ${r[0].used_url})` : "");
        out.style.color = r.every(x => x.ok) ? "var(--ok)" : "var(--danger)";
      } catch (e) { out.textContent = "✖ " + e.message; out.style.color = "var(--danger)"; }
    });
  };
  const collect = () => $$("#a-list .panel[data-n]").forEach(p => {
    const x = insts[+p.dataset.n];
    $$("[data-f]", p).forEach(el => {
      if (el.dataset.f === "indexer_ids") x.indexer_ids = el.value.split(",").map(v => v.trim()).filter(Boolean);
      else x[el.dataset.f] = el.type === "checkbox" ? el.checked : el.value.trim();
    });
  });
  render();
};

/* ---------- stats ---------- */
pages.stats = async () => {
  const s = await api("/stats").catch(() => ({summary: [], timeline: []}));
  const byIdx = {};
  s.summary.forEach(r => { (byIdx[r.indexer_id] ||= {})[r.event] = r.count; });
  const totals = {search: 0, grab: 0, download_ok: 0, download_fail: 0};
  s.summary.forEach(r => totals[r.event] = (totals[r.event] || 0) + r.count);
  $("#page").innerHTML = `<h1>Statistics</h1>
    <div class="stat-cards">
      <div class="stat-card"><div class="num">${totals.search || 0}</div><div class="lbl">Searches</div></div>
      <div class="stat-card"><div class="num">${totals.grab || 0}</div><div class="lbl">Grabs</div></div>
      <div class="stat-card"><div class="num">${totals.download_ok || 0}</div><div class="lbl">Downloads completed</div></div>
      <div class="stat-card"><div class="num">${totals.download_fail || 0}</div><div class="lbl">Downloads failed</div></div>
    </div>
    <table><thead><tr><th>Indexer</th><th>Searches</th><th>Grabs</th><th>Completed</th><th>Failed</th></tr></thead><tbody>
    ${Object.entries(byIdx).map(([id, e]) => `<tr><td>${esc(id)}</td><td>${e.search||0}</td><td>${e.grab||0}</td><td>${e.download_ok||0}</td><td>${e.download_fail||0}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">No activity recorded yet.</td></tr>`}
    </tbody></table>
    <p class="dim">Prometheus metrics are exported at <code>/metrics</code>.</p>`;
};

/* ---------- settings ---------- */
pages.settings = async () => {
  const [s, st] = await Promise.all([api("/settings").catch(() => null), fetch("/ui/auth/state").then(r => r.json())]);
  if (!s) return;
  const F = (sec, key, label, type = "text", hint = "") => `<label class="field"><span>${label}</span>
    <input data-sec="${sec}" data-key="${key}" type="${type}" value="${esc(s[sec][key])}">${hint ? `<span class="hint">${hint}</span>` : ""}</label>`;
  const SEL = (sec, key, opts, label, hint = "") => `<label class="field"><span>${label}</span>
    <select data-sec="${sec}" data-key="${key}">${opts.map(o => { const [v, l] = Array.isArray(o) ? o : [o, o];
      return `<option value="${v}" ${s[sec][key] == v ? "selected" : ""}>${l}</option>`; }).join("")}</select>${hint ? `<span class="hint">${hint}</span>` : ""}</label>`;
  const C = (sec, key, label) => `<label class="check"><input type="checkbox" data-sec="${sec}" data-key="${key}" ${s[sec][key] ? "checked" : ""}> ${label}</label>`;
  $("#page").innerHTML = `<h1>Settings <button class="btn accent" id="set-save">Save</button></h1>
    <div class="panel"><h3>General</h3>
      ${F("streamarr","port","Port","number",
        "The port Streamarr listens on inside the container. Takes effect after a restart; update your Docker port mapping to match (e.g. <code>-p 8585:&lt;port&gt;</code>).")}
      ${F("streamarr","public_url","Streamarr URL (how other services reach this instance)","text",
        `Used for connection tips and auto-configuration. Leave empty to auto-guess — current guess: <code>${esc(s.url_guess)}</code>. Inside docker-compose the service name works: <code>http://streamarr:8585</code>.`)}
    </div>
    <div class="panel"><h3>Security</h3>
      <label class="field"><span>Login mode</span><select id="sec-mode">
        <option value="none" ${st.mode === "none" ? "selected" : ""}>No login (open)</option>
        <option value="local" ${st.mode === "local" ? "selected" : ""}>No login on local networks, login from elsewhere</option>
        <option value="forms" ${st.mode === "forms" ? "selected" : ""}>Login required</option>
      </select></label>
      <div class="form-grid">
        <label class="field"><span>Username</span><input id="sec-user"></label>
        <label class="field"><span>Password</span><input id="sec-pass" type="password"></label>
      </div>
      <button class="btn" id="sec-account">Set account</button>
      <span id="sec-out" class="dim"></span>
      <hr style="border-color:var(--border);margin:14px 0">
      <label class="field"><span>API key (Newznab · SABnzbd · UI API)</span>
        <span style="display:flex;gap:8px"><input id="sec-key" readonly value="${esc(s.streamarr.api_key)}" style="flex:1">
        <button class="btn" id="sec-copy">Copy</button>
        <button class="btn danger" id="sec-rotate">Rotate</button></span></label>
      <div class="hint">Rotating re-pushes the new key to all auto-configured arr instances; manually connected ones must be updated by hand.</div></div>
    <div class="panel"><h3>Downloads</h3><div class="form-grid">
      ${F("downloads","path","Download path","text","Completed files land in <code>&lt;path&gt;/&lt;category&gt;/&lt;release&gt;/</code> — mount the same volume into your arr apps.")}
      ${F("downloads","speed_limit_kbps","Speed limit (KB/s, 0 = unlimited)","number","Also adjustable from the queue page and via the SABnzbd API.")}
      ${F("downloads","max_concurrent","Max concurrent downloads","number","Reserved — downloads currently run one at a time to stay friendly with upstream rate limits.")}</div></div>
    <div class="panel"><h3>Quality — global defaults</h3>
      <div class="hint">Each indexer can override these under Indexers → Quality.</div>
      <div class="form-grid">
      ${SEL("quality","max_resolution",RES_OPTS,"Max resolution")}
      ${SEL("quality","max_fps",FPS_OPTS,"Max FPS")}
      ${SEL("quality","video_format",VFMT_OPTS,"Video container")}
      ${SEL("quality","audio_codec",ACODEC_OPTS,"Preferred audio codec")}
      ${SEL("quality","audio_format",AFMT_OPTS,"Audio-only format")}</div></div>
    <div class="panel"><h3>SponsorBlock (YouTube)</h3>
      ${C("sponsorblock","enabled","Remove sponsored segments")}
      <label class="field"><span>Categories (comma separated: sponsor, intro, outro, selfpromo…)</span>
      <input data-sec="sponsorblock" data-key="categories" value="${esc(s.sponsorblock.categories.join(","))}"></label></div>
    <div class="panel"><h3>Rate limiting & backoff</h3>
      <div class="hint">Streamarr spaces upstream requests and backs off automatically when a provider signals overload (HTTP 429/503).</div>
      <div class="form-grid">
      ${F("ratelimit","sleep_requests","Seconds between upstream API requests","number")}
      ${F("ratelimit","download_delay","Seconds between downloads","number")}
      ${F("ratelimit","rate_limit_sleep","Initial backoff when rate limited (s)","number","Default 300 = 5 minutes.")}
      ${F("ratelimit","backoff_multiplier","Backoff multiplier","number")}
      ${F("ratelimit","backoff_max","Max backoff (s)","number")}</div>
      ${C("ratelimit","exponential_backoff","Exponential backoff")}</div>
    <div class="panel"><h3>Cache</h3><div class="form-grid">
      ${F("cache","retention_days","Retention (days)","number","How long cached source listings and search results are kept in the SQLite cache.")}
      ${F("cache","refresh_minutes","Source refresh interval (min)","number","How often channel/source lists are re-fetched from the site.")}</div></div>
    <div class="panel"><h3>Subscriptions</h3>
      <div class="hint">Channels/playlists subscribed on the Indexers page are checked periodically; new releases are queued automatically.</div>
      ${C("subscriptions","enabled","Enable automatic subscription downloads")}
      ${C("subscriptions","check_arr","Cross-check with connected arr instances — skip releases that are already imported")}
      <div class="form-grid">${F("subscriptions","interval_minutes","Check interval (minutes)","number")}</div>
      <button class="btn" id="subs-now">Check subscriptions now</button> <span id="subs-out" class="dim"></span></div>
    <div class="panel"><h3>yt-dlp</h3>
      ${C("ytdlp","auto_update","Auto-update yt-dlp (on every start and on the interval below)")}
      ${C("ytdlp","restart_after_update","Restart Streamarr after an update (waits for active downloads to finish)")}
      <div class="form-grid">${F("ytdlp","update_interval_hours","Update interval (hours)","number")}</div>
      <button class="btn" id="ytdlp-now">Update yt-dlp now</button> <span id="ytdlp-out" class="dim"></span></div>
    <div class="panel"><h3>Proxy (upstream streaming traffic and arr connections)</h3>
      ${C("proxy","enabled","Use proxy")}
      ${C("proxy","bypass_local","Bypass proxy for local addresses (RFC1918, loopback, bare hostnames)")}
      <label class="field"><span>Ignored addresses (comma separated, wildcards allowed — like in the arr apps)</span>
        <input data-sec="proxy" data-key="ignored_addresses" value="${esc(s.proxy.ignored_addresses || "")}" placeholder="192.168.1.*, *.lan, sonarr"></label>
      <div class="form-grid">
        <label class="field"><span>Type</span><select data-sec="proxy" data-key="type">
          ${["http","https","socks4","socks5"].map(t => `<option ${s.proxy.type===t?"selected":""}>${t}</option>`).join("")}</select></label>
        ${F("proxy","host","Host")}${F("proxy","port","Port","number")}
        ${F("proxy","username","Username (optional)")}${F("proxy","password","Password (optional)","password")}</div></div>
    <div class="panel"><h3>Backup</h3>
      <a class="btn" href="/ui/backup">Download backup</a>
      <label class="btn">Restore backup <input type="file" id="restore-file" accept=".zip" hidden></label>
      <span id="restore-out" class="dim"></span></div>`;
  $("#sec-mode").onchange = async () => {
    try { await api("/auth/mode", {json: {mode: $("#sec-mode").value}}); toastOk("Login mode updated"); }
    catch (e) { toastErr(e); $("#sec-mode").value = st.mode; }
  };
  $("#sec-account").onclick = async () => {
    try {
      await api("/auth/setup", {json: {username: $("#sec-user").value, password: await sha256hex($("#sec-pass").value)}});
      $("#sec-out").textContent = "✔ account set, login mode = required"; $("#sec-mode").value = "forms";
    } catch (e) { $("#sec-out").textContent = "✖ " + e.message; }
  };
  $("#sec-copy").onclick = async () => {
    const r = await api("/apikey", {method: "POST"}).catch(toastErr);
    if (!r) return;
    try { await navigator.clipboard.writeText(r.api_key); toastOk("API key copied"); }
    catch {
      const ta = document.createElement("textarea"); ta.value = r.api_key;
      document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
      toastOk("API key copied");
    }
  };
  $("#sec-rotate").onclick = async () => {
    if (!confirm("Rotate the API key? Manually connected arr apps will stop working until updated.")) return;
    const r = await api("/apikey/rotate", {method: "POST"}).catch(toastErr);
    if (r) { $("#sec-key").value = r.api_key; toastOk("API key rotated — copy provides the full key"); }
  };
  $("#set-save").onclick = async () => {
    const bySec = {};
    $$("#page [data-sec]").forEach(el => {
      const sec = el.dataset.sec, key = el.dataset.key;
      let v = el.type === "checkbox" ? el.checked : el.value;
      if (el.type === "number" || (el.tagName === "SELECT" && !isNaN(+v) && v !== "")) v = +v;
      if (sec === "sponsorblock" && key === "categories") v = String(v).split(",").map(x => x.trim()).filter(Boolean);
      if (sec === "quality" && ["video_format","audio_codec","audio_format"].includes(key)) v = el.value;
      (bySec[sec] ||= {})[key] = v;
    });
    try { for (const [sec, vals] of Object.entries(bySec)) await api("/settings/" + sec, {json: vals}); toastOk("Settings saved"); }
    catch (e) { toastErr(e); }
  };
  $("#subs-now").onclick = async () => {
    $("#subs-out").textContent = "Running…";
    const r = await api("/subscriptions/run", {method: "POST"}).catch(e => ({detail: e.message}));
    $("#subs-out").textContent = r.detail;
  };
  $("#ytdlp-now").onclick = async () => {
    $("#ytdlp-out").textContent = "Updating…";
    const r = await api("/ytdlp/update", {method: "POST"}).catch(e => ({ok: false, detail: e.message}));
    $("#ytdlp-out").textContent = (r.ok ? "✔ " : "✖ ") + r.detail;
  };
  $("#restore-file").onchange = async e => {
    const fd = new FormData(); fd.append("file", e.target.files[0]);
    $("#restore-out").textContent = "Restoring…";
    try { const r = await api("/restore", {method: "POST", body: fd}); $("#restore-out").textContent = "✔ " + r.detail; }
    catch (ex) { $("#restore-out").textContent = "✖ " + ex.message; }
  };
};

/* ---------- logs ---------- */
pages.logs = () => {
  $("#page").innerHTML = `<h1>Logs</h1>
    <div class="toolbar"><select id="log-level">
      ${["", "DEBUG", "INFO", "WARNING", "ERROR"].map(l => `<option value="${l}">${l || "All levels"}</option>`).join("")}
    </select><button class="btn" id="log-copy">Copy log</button></div>
    <div class="loglines" id="loglines"></div>`;
  $("#log-copy").onclick = async () => {
    const text = $$("#loglines div").map(d => d.textContent).join("\n");
    try { await navigator.clipboard.writeText(text); toastOk("Log copied to clipboard"); }
    catch { const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta);
      ta.select(); document.execCommand("copy"); ta.remove(); toastOk("Log copied to clipboard"); }
  };
  const render = async () => {
    const lines = await api("/logs?level=" + $("#log-level").value).catch(() => []);
    const el = $("#loglines");
    const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
    el.innerHTML = lines.map(l => `<div class="log-${l.level}">${new Date(l.ts * 1000).toLocaleTimeString()} ${l.level.padEnd(7)} ${esc(l.logger)}: ${esc(l.message)}</div>`).join("");
    if (stick) el.scrollTop = el.scrollHeight;
  };
  $("#log-level").onchange = render;
  render();
  logTimer = setInterval(render, 3000);
};

/* ---------- system ---------- */
pages.system = async () => {
  const [s, h] = await Promise.all([api("/status").catch(() => null), api("/health/instances").catch(() => [])]);
  $("#page").innerHTML = `<h1>System</h1>
    <div class="panel"><h3>About</h3>
      <div>Streamarr v${s ? s.version : "?"}</div>
      <div>yt-dlp ${s ? s.ytdlp_version : "?"}</div>
      <div>Prometheus metrics: <code>/metrics</code> · Healthcheck: <code>/ping</code></div></div>
    <div class="panel"><h3>Connected instances</h3>
      ${h.map(i => `<div style="margin:4px 0"><span class="pill ${i.ok === null ? "" : i.ok ? "ok" : "err"}">${i.ok === null ? "disabled" : i.ok ? "online" : "offline"}</span> ${esc(i.name)} <span class="dim">${esc(i.detail)}</span></div>`).join("") || `<div class="empty">None configured.</div>`}
    </div>`;
};

/* ---------- boot ---------- */
applyTheme();
initAuth();
setInterval(pollStatus, 2000);
setInterval(pollHealth, 30000);
pollStatus();
setTimeout(pollHealth, 1500);
