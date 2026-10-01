/* ===========================================================================
   Folder Converter — Trimmen
   Lädt die ausgewählten Videos und Audios untereinander (Videovorschau,
   Filmstreifen, Wellenform) und schneidet sie per Griffen oder Zeitfeldern
   zu. Nutzt die Helfer aus app.js ($, api, toast, notice, S, …).
   =========================================================================== */

const T = {
  open: false,
  gen: 0,              // erhöht sich bei jedem Öffnen — verspätete Antworten verwerfen
  clips: [],           // [{f, info, start, end, playhead, el, media, peaks, ...}]
  fast: false,
};
const MIN_LEN = 0.1;   // kürzeste Auswahl in Sekunden

const ICON = {
  play: '<svg viewBox="0 0 29.195 33.368" width="13" height="15" fill="currentColor"><path d="M27.658 13.99 4.718.428A3.111 3.111 0 0 0 0 3.12v27.117a3.125 3.125 0 0 0 4.718 2.692l22.94-13.555a3.125 3.125 0 0 0 0-5.384"></path></svg>',
  pause: '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><rect x="5" y="4" width="5" height="16" rx="1.4"></rect><rect x="14" y="4" width="5" height="16" rx="1.4"></rect></svg>',
  reset: '<svg viewBox="0 0 42.717 42.688" width="15" height="15" fill="currentColor"><path d="M1.579,0H5.595A1.016,1.016,0,0,1,6.611,1.065l-.339,7.01A21.006,21.006,0,1,1,8.2,37.275,1.016,1.016,0,0,1,8.158,35.8l2.88-2.88a1.016,1.016,0,0,1,1.387-.047A14.907,14.907,0,1,0,9.9,13.366l8.6-.412a1.016,1.016,0,0,1,1.065,1.016v4.016A1.016,1.016,0,0,1,18.547,19H1.579A1.016,1.016,0,0,1,.562,17.985V1.016A1.016,1.016,0,0,1,1.579,0Z" transform="translate(-0.563)"></path></svg>',
  scissors: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="3"></circle><circle cx="6" cy="18" r="3"></circle><path d="M20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12"></path></svg>',
};
const MIME = {
  mp4: "video/mp4", m4v: "video/mp4", mov: "video/quicktime", webm: "video/webm", mkv: "video/x-matroska",
  avi: "video/x-msvideo", wmv: "video/x-ms-wmv", flv: "video/x-flv", mpg: "video/mpeg", mpeg: "video/mpeg",
  mp3: "audio/mpeg", wav: "audio/wav", flac: "audio/flac", aac: "audio/aac", m4a: "audio/mp4",
  ogg: "audio/ogg", opus: 'audio/ogg; codecs="opus"', wma: "audio/x-ms-wma",
};

/* ---------- Zeiten ---------- */
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/* 75.25 -> "1:15,25" (wie fmt_time in converter_core) */
function fmtTime(sec, dec = 2) {
  const t = Math.round(Math.max(0, sec) * 10 ** dec) / 10 ** dec;
  const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t - h * 3600 - m * 60;
  const ss = (s < 10 ? "0" : "") + s.toFixed(dec).replace(".", ",");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/* "12", "12,5", "1:02,5", "1:02:03" -> Sekunden (NaN, wenn unlesbar) */
function parseTime(str) {
  const m = String(str).trim().replace(",", ".").match(/^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d*)?|\.\d+)$/);
  if (!m) return NaN;
  const nums = [m[1], m[2], m[3]].filter(x => x !== undefined).map(Number);
  return nums.reduce((acc, n) => acc * 60 + n, 0);
}

/* ---------- Öffnen / Schließen ---------- */
function trimCandidates() { return selectedShown().filter(f => f.kind !== "image"); }

function updateTrimButton() {
  const n = trimCandidates().length;
  $("#btnTrim").disabled = !n || S.running;
  $("#trimLabel").textContent = n ? `Trimmen (${n})` : "Trimmen";
  $("#btnTrim").title = n ? "Ausgewählte Videos und Audios auf die gewünschte Länge zuschneiden"
                          : "Erst Videos oder Audios in der Liste auswählen";
}

function openTrim() {
  const files = trimCandidates();
  if (!files.length || S.running) return;
  const images = selectedShown().length - files.length;
  T.open = true; T.gen++;
  T.clips = files.map(f => ({ f, info: null, start: 0, end: 0, playhead: 0, peaks: null }));
  $("#mainView").hidden = true;
  $("#trimView").hidden = false;
  $("#trimView").scrollTop = 0;
  $("#trimSub").textContent = `${files.length} Datei(en) — Bereich mit den Griffen ziehen oder Zeiten eintragen`
    + (images ? ` · ${images} Bild(er) übersprungen` : "");
  $("#trimOut").textContent = `Ausgabe: ${S.folder.path}/trim - converted — die Originale bleiben unverändert`;
  const box = $("#clips");
  box.innerHTML = "";
  T.clips.forEach((c, i) => { c.el = clipCard(c, i); box.appendChild(c.el); });
  runQueue(T.clips, 3, loadClip, T.gen);
  updateFooter();
}

function closeTrim() {
  if (!T.open) return;
  for (const c of T.clips) {
    if (c.media) { c.media.pause(); c.media.removeAttribute("src"); c.media.load(); }
  }
  T.open = false; T.gen++; T.clips = [];
  $("#clips").innerHTML = "";
  $("#trimView").hidden = true;
  $("#mainView").hidden = false;
  updateSel();
}

async function runQueue(items, workers, fn, gen) {
  let next = 0;
  const work = async () => {
    while (next < items.length && gen === T.gen) await fn(items[next++], gen);
  };
  await Promise.all(Array.from({ length: workers }, work));
}

/* ---------- Karte je Datei ---------- */
function clipCard(c, i) {
  const f = c.f, video = f.kind === "video";
  return el(`<div class="card clip ${f.kind} loading" data-i="${i}">
    <div class="cliphead">
      <div class="ckind">${KIND_SVG[f.kind]}</div>
      <div class="t">
        <div class="cname" title="${esc(f.rel)}"><span>${esc(f.dir)}</span>${esc(f.name)}</div>
        <div class="meta" data-meta>wird geladen …</div>
      </div>
      <label class="num tc"><span class="cap">Start</span><span class="val"><input data-f="start" spellcheck="false" inputmode="decimal"></span></label>
      <label class="num tc"><span class="cap">Ende</span><span class="val"><input data-f="end" spellcheck="false" inputmode="decimal"></span></label>
      <div class="len"><div class="cap">Länge</div><div class="lv" data-len>–</div></div>
      <button class="btn quiet isq" data-a="play" title="Auswahl abspielen">${ICON.play}</button>
      <button class="btn quiet isq" data-a="reset" title="Ganze Datei (Auswahl zurücksetzen)">${ICON.reset}</button>
      <button class="btn turq sm" data-a="trim" title="Nur diese Datei zuschneiden und speichern">${ICON.scissors} Trimmen</button>
    </div>
    <div class="clipbody">
      ${video ? `<div class="vidbox"><video preload="metadata" playsinline></video><div class="vmsg" data-msg>Vorschau wird geladen …</div></div>`
              : `<audio preload="metadata"></audio>`}
      <div class="timeline">
        <div class="track" data-track>
          ${video ? '<div class="strip" data-strip></div>' : ""}
          <canvas class="wave" data-wave></canvas>
          ${video ? "" : '<div class="tmsg" data-msg>Wellenform wird geladen …</div>'}
          <div class="shade l" data-sl></div><div class="shade r" data-sr></div>
          <div class="sel" data-sel><div class="hd l" data-h="start"></div><div class="hd r" data-h="end"></div></div>
          <div class="playhead" data-ph></div>
        </div>
        <div class="ticks" data-ticks></div>
      </div>
    </div>
    <div class="cstatus" data-status></div>
  </div>`);
}

const q = (c, sel) => c.el.querySelector(sel);

function setMsg(c, text) {
  const m = q(c, "[data-msg]");
  if (m) { m.textContent = text || ""; m.hidden = !text; }
}

function setStatus(c, text, kind = "") {
  const s = q(c, "[data-status]");
  s.textContent = text || "";
  s.className = "cstatus" + (kind ? " " + kind : "");
}

async function loadClip(c, gen) {
  const info = await api("trim_info", c.f.rel);
  if (gen !== T.gen) return;
  c.el.classList.remove("loading");
  if (!info) { c.el.classList.add("broken"); q(c, "[data-meta]").textContent = "Diese Datei lässt sich nicht lesen."; return; }
  c.info = info; c.start = 0; c.end = info.duration; c.playhead = 0;
  updateFooter();
  const kind = c.f.kind === "video" ? "Video" : "Audio";
  q(c, "[data-meta]").textContent = [
    kind, info.width ? `${info.width}×${info.height}` : "", `Länge ${fmtTime(info.duration)}`,
    info.audio ? "" : "ohne Ton",
  ].filter(Boolean).join(" · ");
  if (!info.audio) c.el.classList.add("silent");
  paint(c); drawTicks(c);
  attachMedia(c, info.url);

  const jobs = [];
  if (info.audio) {
    jobs.push(api("trim_waveform", c.f.rel, 1200).then(r => {
      if (gen !== T.gen) return;
      c.peaks = r?.peaks || [];
      drawWave(c);
      if (c.f.kind !== "video") setMsg(c, c.peaks.length ? "" : "Keine Wellenform verfügbar");
    }));
  }
  if (c.f.kind === "video") {
    const track = q(c, "[data-track]");
    const aspect = info.width && info.height ? info.width / info.height : 16 / 9;
    const count = clamp(Math.round(track.clientWidth / (60 * aspect)), 4, 30);
    jobs.push(api("trim_filmstrip", c.f.rel, count).then(r => {
      if (gen !== T.gen || !r?.src) return;
      q(c, "[data-strip]").style.backgroundImage = `url("${r.src}")`;
    }));
  }
  await Promise.all(jobs);
}

/* Abspielen über den lokalen Server; was die WebView nicht kann, bekommt eine Vorschau-Kopie. */
function attachMedia(c, url) {
  const m = q(c, "video, audio");
  c.media = m;
  const needsProxy = !m.canPlayType(MIME[c.f.ext] || "");
  m.addEventListener("loadedmetadata", () => {
    if (c.f.kind !== "video") return;
    setMsg(c, "");
    // WebKit zeichnet ohne Abspielen kein Bild — kurz anspringen zeigt das Standbild.
    m.currentTime = c.playhead || Math.min(0.04, m.duration / 2 || 0);
  });
  m.addEventListener("error", () => useProxy(c));
  m.addEventListener("play", () => { playButton(c, true); followPlayback(c); });
  m.addEventListener("pause", () => playButton(c, false));
  if (needsProxy) useProxy(c); else m.src = url;
}

async function useProxy(c) {
  if (c.proxyTried || !c.media) return;
  c.proxyTried = true;
  setMsg(c, "Vorschau wird erstellt …");
  const gen = T.gen;
  const r = await api("trim_proxy", c.f.rel);
  if (gen !== T.gen) return;
  if (!r) { c.noPreview = true; setMsg(c, "Keine Vorschau — die Zeiten lassen sich trotzdem einstellen"); return; }
  c.media.src = r.url;
  if (c.f.kind !== "video") setMsg(c, c.peaks?.length === 0 ? "Keine Wellenform verfügbar" : "");
}

function playButton(c, playing) {
  const b = q(c, '[data-a="play"]');
  b.innerHTML = playing ? ICON.pause : ICON.play;
  b.title = playing ? "Anhalten" : "Auswahl abspielen";
}

/* ---------- Anzeige ---------- */
function paint(c) {
  if (!c.info) return;
  const d = c.info.duration, a = c.start / d * 100, b = c.end / d * 100;
  q(c, "[data-sl]").style.width = a + "%";
  q(c, "[data-sr]").style.width = (100 - b) + "%";
  const sel = q(c, "[data-sel]");
  sel.style.left = a + "%"; sel.style.width = (b - a) + "%";
  q(c, "[data-ph]").style.left = (c.playhead / d * 100) + "%";
  for (const k of ["start", "end"]) {
    const inp = q(c, `[data-f="${k}"]`);
    if (document.activeElement !== inp) { inp.value = fmtTime(c[k]); inp.closest(".num").classList.remove("bad"); }
  }
  q(c, "[data-len]").textContent = fmtTime(c.end - c.start);
  c.el.classList.toggle("changed", isChanged(c));
}

function drawWave(c) {
  const cv = q(c, "[data-wave]"), peaks = c.peaks;
  const dpr = window.devicePixelRatio || 1;
  const W = Math.round(cv.clientWidth * dpr), H = Math.round(cv.clientHeight * dpr);
  if (!W || !H) return;
  cv.width = W; cv.height = H;
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, W, H);
  if (!peaks?.length) return;
  let top = 0;
  for (const p of peaks) if (p > top) top = p;
  top = top || 1;
  ctx.fillStyle = "#4A4FC4";
  const bar = 2 * dpr, step = 3 * dpr, n = peaks.length;
  for (let x = 0; x < W; x += step) {
    const i0 = Math.floor(x / W * n), i1 = Math.max(i0 + 1, Math.floor((x + step) / W * n));
    let v = 0;
    for (let i = i0; i < i1 && i < n; i++) if (peaks[i] > v) v = peaks[i];
    const h = Math.max(dpr, v / top * H * 0.9);
    ctx.fillRect(x, (H - h) / 2, bar, h);
  }
}

function drawTicks(c) {
  const d = c.info.duration;
  const step = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200]
    .find(s => d / s <= 8) || 7200;
  const dec = step < 1 ? 1 : 0;
  let html = "";
  for (let t = 0; t <= d + 1e-6; t += step) {
    html += `<span style="left:${t / d * 100}%">${fmtTime(t, dec)}</span>`;
  }
  q(c, "[data-ticks]").innerHTML = html;
}

/* ---------- Bedienung ---------- */
const isChanged = (c) => !!c.info && (c.start > 0.005 || c.end < c.info.duration - 0.005);

function seekTo(c, t) {
  c.playhead = clamp(t, 0, c.info.duration);
  if (c.media && !c.noPreview && c.media.readyState > 0) c.media.currentTime = c.playhead;
  q(c, "[data-ph]").style.left = (c.playhead / c.info.duration * 100) + "%";
}

function onTrackDown(e, c) {
  if (!c.info || e.button !== 0) return;
  e.preventDefault();
  const track = q(c, "[data-track]"), rect = track.getBoundingClientRect(), d = c.info.duration;
  const at = (x) => Math.round(clamp((x - rect.left) / rect.width, 0, 1) * d * 100) / 100;
  const mode = e.target.dataset.h || (e.target.closest("[data-sel]") ? "move" : "seek");
  const x0 = e.clientX, t0 = at(x0), s0 = c.start, e0 = c.end;
  let moved = false;
  const apply = (x) => {
    const t = at(x);
    if (Math.abs(x - x0) > 3) moved = true;
    if (mode === "start") { c.start = clamp(t, 0, c.end - MIN_LEN); seekTo(c, c.start); }
    else if (mode === "end") { c.end = clamp(t, c.start + MIN_LEN, d); seekTo(c, c.end); }
    else if (mode === "move" && moved) {
      const len = e0 - s0, s = clamp(s0 + t - t0, 0, d - len);
      c.start = s; c.end = s + len; seekTo(c, s);
    } else if (mode === "seek") seekTo(c, t);
    paint(c);
  };
  if (c.media && !c.media.paused) c.media.pause();
  apply(x0);
  const move = (ev) => apply(ev.clientX);
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
    if (mode === "move" && !moved) { seekTo(c, t0); paint(c); }   // Klick in die Auswahl = Position setzen
    updateFooter();
  };
  document.addEventListener("mousemove", move);
  document.addEventListener("mouseup", up);
}

function onTimeField(c, inp, commit) {
  const t = parseTime(inp.value), d = c.info?.duration;
  const lo = inp.dataset.f === "start" ? 0 : c.start + MIN_LEN;
  const hi = inp.dataset.f === "start" ? c.end - MIN_LEN : d;
  const ok = c.info && !Number.isNaN(t) && t >= lo - 0.005 && t <= hi + 0.005;
  inp.closest(".num").classList.toggle("bad", !ok);
  if (!ok || !commit) return;
  c[inp.dataset.f] = clamp(t, lo, hi);
  seekTo(c, c[inp.dataset.f]);
  inp.blur();
  paint(c);
  updateFooter();
}

function togglePlay(c) {
  const m = c.media;
  if (!c.info || !m || c.noPreview) return;
  if (!m.paused) { m.pause(); return; }
  for (const o of T.clips) if (o !== c && o.media && !o.media.paused) o.media.pause();
  const from = c.playhead >= c.start && c.playhead < c.end - 0.05 ? c.playhead : c.start;
  m.currentTime = from;
  m.play().catch(() => toast("Diese Datei lässt sich hier nicht abspielen.", "err"));
}

/* Während der Wiedergabe den Abspielkopf mitführen und am Auswahlende stoppen. */
function followPlayback(c) {
  const step = () => {
    const m = c.media;
    if (!m || m.paused || !T.open) return;
    c.playhead = m.currentTime;
    if (m.currentTime >= c.end) { m.pause(); c.playhead = c.end; }
    q(c, "[data-ph]").style.left = (c.playhead / c.info.duration * 100) + "%";
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function resetClip(c) {
  if (!c.info) return;
  c.start = 0; c.end = c.info.duration;
  setStatus(c, "");
  paint(c); updateFooter();
}

function applyAll() {
  const a = parseField($("#allStart")), b = parseField($("#allEnd"));
  for (const [inp, r] of [[$("#allStart"), a], [$("#allEnd"), b]]) {
    validateField(inp);
    inp.closest(".num").classList.toggle("bad", !!r.bad || r.v < 0);
  }
  if (a.bad || b.bad || a.v < 0 || b.v < 0) { notice("Bitte Sekunden eintragen, z. B. 1,5."); return; }
  let tooShort = 0, done = 0;
  for (const c of T.clips) {
    if (!c.info) continue;
    const s = a.v, e = c.info.duration - b.v;
    if (e - s < MIN_LEN) {
      tooShort++;
      setStatus(c, `Zu kurz für ${fmt(a.v, "float")} s + ${fmt(b.v, "float")} s — unverändert gelassen.`, "err");
      continue;
    }
    c.start = s; c.end = e; seekTo(c, s); setStatus(c, ""); paint(c); done++;
  }
  updateFooter();
  if (tooShort) toast(`${done} Datei(en) angepasst, ${tooShort} sind dafür zu kurz.`, "err");
  else toast(`Bei ${done} Datei(en) angewendet.`, "ok");
}

/* ---------- Speichern ---------- */
async function runTrim(list) {
  if (S.running || !list.length) return;
  const r = await api("trim_start", list.map(c => ({ rel: c.f.rel, start: c.start, end: c.end })), T.fast);
  if (!r) return;
  for (const c of list) setStatus(c, "wartet …");
  S.lastJob = r.job;
  if (S.finishedJob !== r.job) setRunning(true);
}

function trimOne(c) {
  if (!c.info) return;
  if (!isChanged(c)) { notice("Die Auswahl umfasst die ganze Datei — erst Griffe ziehen oder Zeiten eintragen."); return; }
  runTrim([c]);
}

function trimAll() {
  const list = T.clips.filter(isChanged);
  if (!list.length) { notice("Noch keine Datei gekürzt — Griffe ziehen, Zeiten eintragen oder „Für alle“ nutzen."); return; }
  runTrim(list);
}

/* Wird von finish() in app.js aufgerufen. */
function trimResults(o) {
  if (!T.open) return;
  const byRel = new Map((o.results || []).map(r => [r.rel, r]));
  for (const c of T.clips) {
    const r = byRel.get(c.f.rel);
    if (r) setStatus(c, r.ok ? `✓ Gespeichert: ${r.out}` : `✗ ${r.error}`, r.ok ? "ok" : "err");
    else if (q(c, "[data-status]").textContent === "wartet …") setStatus(c, "Abgebrochen", "err");
  }
}

function trimSummary() {
  const loaded = T.clips.filter(c => c.info).length;
  if (loaded < T.clips.length) return `${loaded} von ${T.clips.length} Dateien geladen …`;
  const n = T.clips.filter(isChanged).length;
  return `${n} von ${T.clips.length} Datei(en) gekürzt`;
}

function initTrim() {
  $("#btnTrim").onclick = openTrim;
  $("#trimBack").onclick = closeTrim;
  $("#trimApplyAll").onclick = applyAll;
  $("#trimFast").onclick = () => { T.fast = !T.fast; setCheck($("#trimFast"), T.fast); };
  for (const id of ["allStart", "allEnd"]) {
    const inp = $("#" + id);
    validateField(inp);
    inp.addEventListener("input", () => { validateField(inp); inp.closest(".num").classList.remove("bad"); });
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); applyAll(); } });
  }
  const box = $("#clips");
  const clipOf = (node) => T.clips[parseInt(node.closest(".clip")?.dataset.i, 10)];
  box.addEventListener("mousedown", (e) => {
    const track = e.target.closest("[data-track]");
    if (track) onTrackDown(e, clipOf(track));
  });
  box.addEventListener("click", (e) => {
    const b = e.target.closest("[data-a]");
    if (!b) return;
    const c = clipOf(b);
    if (b.dataset.a === "play") togglePlay(c);
    else if (b.dataset.a === "reset") resetClip(c);
    else if (b.dataset.a === "trim") trimOne(c);
  });
  box.addEventListener("input", (e) => { if (e.target.dataset.f) onTimeField(clipOf(e.target), e.target, false); });
  box.addEventListener("change", (e) => { if (e.target.dataset.f) onTimeField(clipOf(e.target), e.target, true); });
  box.addEventListener("keydown", (e) => {
    if (e.target.dataset.f && e.key === "Enter") { e.preventDefault(); onTimeField(clipOf(e.target), e.target, true); }
    if (e.target.dataset.f && e.key === "Escape") { e.stopPropagation(); e.target.blur(); paint(clipOf(e.target)); }
  });
  box.addEventListener("focusout", (e) => {
    if (e.target.dataset.f) { const c = clipOf(e.target); if (c) setTimeout(() => paint(c)); }
  });
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => T.clips.forEach(c => { if (c.info) { drawWave(c); drawTicks(c); } }), 120);
  });
}
