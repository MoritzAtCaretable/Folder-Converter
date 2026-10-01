/* ===========================================================================
   Folder Converter — Oberfläche
   Rendert das Caretable-Design und ruft die Python-Logik über
   window.pywebview.api auf. Die Konvertierung liegt vollständig im Backend.
   =========================================================================== */

const S = {
  defaults: {},        // DEFAULT_FORM aus converter_core
  defaultCrf: {},
  formats: { video: [], audio: [], image: [] },
  presets: [],         // [{name, values}]
  preset: "",          // zuletzt geladenes Preset
  segs: {},            // Werte der Segment-Schalter (target, resize_op, …)
  folder: null,        // {name, path}
  files: [],           // [{rel, dir, name, ext, kind}]
  filter: "Alle",
  shown: [],           // Dateien nach Filter
  sel: new Set(),      // ausgewählte rel-Pfade
  anchor: -1,          // Index in S.shown für Shift-Klick
  drag: null,          // Auswahl per Ziehen über die Liste
  running: false,
  phase: "idle",       // idle | done | cancelled | failed
  outcome: null,
  lastJob: "",
  finishedJob: "",     // zuletzt abgeschlossener Lauf (gegen verspätete Antworten)
  updating: false,
};

/* ---------- kleine Helfer ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const el = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };

async function api(name, ...args) {
  try {
    const res = await window.pywebview.api[name](...args);
    if (res && res.ok === false) { toast(res.error || "Unbekannter Fehler", "err"); return null; }
    return res || {};
  } catch (e) { toast("Fehler: " + e, "err"); return null; }
}

function toast(msg, kind = "") {
  const t = el(`<div class="toast ${kind}">${esc(msg)}</div>`);
  $("#toast").appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 300); },
    kind === "err" ? 6500 : 3400);
}

/* Hinweis, der im Protokoll landet und kurz eingeblendet wird. */
function notice(msg) { addLog([msg]); toast(msg, "err"); }

const START_ICON = '<svg id="startIcon" viewBox="0 0 29.195 33.368" width="16" height="18" fill="currentColor"><path d="M27.658 13.99 4.718.428A3.111 3.111 0 0 0 0 3.12v27.117a3.125 3.125 0 0 0 4.718 2.692l22.94-13.555a3.125 3.125 0 0 0 0-5.384"></path></svg>';
const CHECK_SVG = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7"></path></svg>';
const KIND_SVG = {
  video: '<svg viewBox="0 0 24 24" width="17" height="17"><path d="M4.6 5h10.8A2.6 2.6 0 0 1 18 7.6v1.9l3.1-2.1a.9.9 0 0 1 1.4.7v7.8a.9.9 0 0 1-1.4.7L18 14.5v1.9a2.6 2.6 0 0 1-2.6 2.6H4.6A2.6 2.6 0 0 1 2 16.4V7.6A2.6 2.6 0 0 1 4.6 5Z"></path></svg>',
  audio: '<svg viewBox="0 0 24 24" width="17" height="17"><circle cx="7" cy="17.4" r="3"></circle><circle cx="17.2" cy="15.4" r="3"></circle><path d="M8.3 17.4V6.3l11.2-2.2v11.3h-2.3V7l-6.6 1.3v9.1Z"></path></svg>',
  image: '<svg viewBox="0 0 24 24" width="17" height="17" fill-rule="evenodd"><path d="M4 4h16a2.6 2.6 0 0 1 2.6 2.6v10.8A2.6 2.6 0 0 1 20 20H4a2.6 2.6 0 0 1-2.6-2.6V6.6A2.6 2.6 0 0 1 4 4Zm4 3.6a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm-4.4 10.6h16.8l-5.8-6.6-3.3 3.7-2.5-2.5-5.2 5.4Z"></path></svg>',
};

/* ---------- Dialoge ---------- */
let closeSheet = null;

function openSheet(html, onDismiss, tag = "") {
  const host = $("#modal");
  if (closeSheet) closeSheet();
  host.innerHTML = "";
  host.dataset.tag = tag;
  const sheet = el(html);
  host.appendChild(sheet);
  host.classList.add("on");
  const close = () => {
    if (closeSheet !== close) return;
    closeSheet = null;
    host.classList.remove("on"); host.innerHTML = ""; host.onclick = null; host.dataset.tag = "";
  };
  closeSheet = close;
  host.onclick = (e) => { if (e.target === host) { close(); onDismiss?.(); } };
  sheet.dismiss = () => { close(); onDismiss?.(); };
  return { sheet, close };
}

/* Bestätigungsdialog — liefert true/false. */
function confirmBox({ title, body = "", pre = "", ok = "Weiter", cancel = "Abbrechen", danger = false, tag = "" }) {
  return new Promise(resolve => {
    const { sheet, close } = openSheet(`
      <div class="sheet">
        <h3>${esc(title)}</h3>
        ${body ? `<div class="body">${esc(body)}</div>` : ""}
        ${pre ? `<div class="pre">${esc(pre)}</div>` : ""}
        <div class="acts">
          <button class="btn quiet" data-no>${esc(cancel)}</button>
          <button class="btn ${danger ? "red" : "turq"}" data-yes>${esc(ok)}</button>
        </div>
      </div>`, () => resolve(false), tag);
    $("[data-no]", sheet).onclick = () => { close(); resolve(false); };
    $("[data-yes]", sheet).onclick = () => { close(); resolve(true); };
    $("[data-yes]", sheet).focus();
  });
}

function infoBox({ title, body = "", pre = "" }) {
  const { sheet, close } = openSheet(`
    <div class="sheet">
      <h3>${esc(title)}</h3>
      ${body ? `<div class="body">${esc(body)}</div>` : ""}
      ${pre ? `<div class="pre">${esc(pre)}</div>` : ""}
      <div class="acts"><button class="btn turq" data-yes>OK</button></div>
    </div>`);
  $("[data-yes]", sheet).onclick = close;
  $("[data-yes]", sheet).focus();
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && closeSheet) { e.preventDefault(); $(".sheet")?.dismiss?.(); }
});

/* ---------- Formular (= Ausgabe-Rezept) ---------- */
const INT_RE = /^[+-]?\d+$/;
const FLOAT_RE = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

function parseField(inp) {
  const raw = inp.value.trim(), t = inp.dataset.t;
  if (t === "int") return INT_RE.test(raw) ? { v: parseInt(raw, 10) } : { bad: true, v: raw };
  if (t === "float") {
    const s = raw.replace(",", ".");
    return FLOAT_RE.test(s) ? { v: Number(s) } : { bad: true, v: raw };
  }
  return { v: raw };
}

function readForm() {
  const form = {}, bad = [];
  for (const inp of $$("input[data-k]")) {
    const r = parseField(inp);
    form[inp.dataset.k] = r.v;
    if (r.bad) bad.push(inp);
  }
  for (const c of $$(".check[data-k]")) form[c.dataset.k] = c.classList.contains("on");
  for (const k of Object.keys(S.segs)) form[k] = S.segs[k];
  for (const s of $$("select[data-k]")) form[s.dataset.k] = s.value;
  return { form, bad };
}

const fmt = (v, t) => t === "float" ? String(v).replace(".", ",") : String(v ?? "");

function setSeg(k, v) {
  S.segs[k] = String(v);
  for (const b of $$(`.seg[data-k="${k}"] button`)) {
    const on = b.dataset.v === S.segs[k];
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", on);
  }
}

function setCheck(c, on) {
  c.classList.toggle("on", on);
  c.setAttribute("aria-checked", on);
}

function writeForm(values) {
  for (const inp of $$("input[data-k]")) {
    if (inp.dataset.k in values) inp.value = fmt(values[inp.dataset.k], inp.dataset.t);
    validateField(inp);
  }
  for (const c of $$(".check[data-k]")) if (c.dataset.k in values) setCheck(c, !!values[c.dataset.k]);
  for (const k of new Set($$(".seg[data-k]").map(s => s.dataset.k))) if (k in values) setSeg(k, values[k]);
  for (const s of $$("select[data-k]")) {
    if (!(s.dataset.k in values)) continue;
    const v = String(values[s.dataset.k]);
    // Handeditierte Presets können Werte haben, die das Menü nicht kennt — trotzdem behalten.
    if (!Array.from(s.options).some(o => o.value === v)) s.add(new Option(v, v));
    s.value = v;
  }
  CtSelects.sync();
  syncForm();
}

function validateField(inp) {
  inp.closest(".num")?.classList.toggle("bad", !!parseField(inp).bad);
  // Felder mit Einheit wachsen mit dem Wert, damit die Einheit direkt dahinter steht.
  if (inp.nextElementSibling?.classList.contains("unit")) {
    inp.style.width = Math.max(1, inp.value.length) + 0.4 + "ch";
  }
}

function sameForm(a, b) {
  for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
    const x = a[k], y = b[k];
    if (typeof x === "number" || typeof y === "number") { if (Number(x) !== Number(y)) return false; }
    else if (x !== y) return false;
  }
  return true;
}

const HEX_RE = /^#?[0-9a-f]{6}$/i;

/* Panels, abgeblendete Felder und Hinweise an das Formular anpassen. */
function syncForm() {
  const { form, bad } = readForm();
  const t = form.target;
  $("#secVideo").hidden = !S.formats.video.includes(t);
  $("#secAudio").hidden = !(S.formats.video.includes(t) || S.formats.audio.includes(t));
  $("#secImage").hidden = !S.formats.image.includes(t);
  for (const d of $$("[data-dim]")) d.classList.toggle("dim", !form[d.dataset.dim]);
  $("#panelColor").hidden = form.img_bg_mode !== "color";
  $("#panelAi").hidden = form.img_bg_mode !== "ai";
  $("#trimHint").textContent = form.img_resize
    ? "danach auf die Zielgröße skaliert" : "danach zurück auf Originalgröße gestreckt";
  const hex = form.img_bg_color;
  $("#swatch").style.background = HEX_RE.test(hex) ? (hex.startsWith("#") ? hex : "#" + hex) : "transparent";

  // Speichern ist nur aktiv, wenn die Einstellungen zu keinem Preset passen.
  const anyMatch = !bad.length && S.presets.some(p => sameForm(form, p.values));
  $("#btnSave").classList.toggle("same", anyMatch);
  $("#btnSave").title = anyMatch ? "Diese Einstellungen sind schon als Preset gespeichert"
                                 : "Aktuelle Einstellungen als Preset speichern";
  const cur = S.presets.find(p => p.name === S.preset);
  const changed = cur && (bad.length || !sameForm(form, cur.values));
  const cap = $("#preset").closest(".ct-select-host")?.querySelector(".ct-select-caption");
  if (cap) cap.textContent = "Preset laden" + (changed ? " · geändert" : "");
  $("#btnDelete").disabled = !cur;
  updateFooter();
}

/* ---------- Presets ---------- */
function renderPresets() {
  const sel = $("#preset");
  const none = S.presets.length ? "Kein Preset geladen" : "Keine Presets gespeichert";
  sel.innerHTML = `<option value="" disabled>${esc(none)}</option>`
    + S.presets.map(p => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join("");
  sel.value = S.presets.some(p => p.name === S.preset) ? S.preset : "";
  if (!sel.value) S.preset = "";
  CtSelects.sync();
  syncForm();
}

function applyPreset() {
  const p = S.presets.find(x => x.name === $("#preset").value);
  if (!p) return;
  S.preset = p.name;
  writeForm({ ...S.defaults, ...p.values });
  addLog(["Preset geladen: " + p.name]);
}

function savePreset() {
  if ($("#btnSave").classList.contains("same")) return;
  const { form, bad } = readForm();
  if (bad.length) { notice("Erst die Zahlenfelder korrigieren, dann das Preset speichern."); return; }
  const { sheet, close } = openSheet(`
    <div class="sheet">
      <h3>Preset speichern</h3>
      <div class="body">Zielformat und alle Parameter werden unter diesem Namen gespeichert.</div>
      <label class="inp"><div class="cap">Name</div>
        <input id="presetName" type="text" maxlength="120" autocomplete="off" spellcheck="false"
               placeholder="z. B. To JPG (1200 px, Qualität 80)"></label>
      <div class="warnline" id="presetWarn"></div>
      <div class="acts">
        <button class="btn quiet" data-no>Abbrechen</button>
        <button class="btn turq" data-yes disabled>Preset speichern</button>
      </div>
    </div>`);
  const input = $("#presetName", sheet), yes = $("[data-yes]", sheet);
  const check = () => {
    const name = input.value.trim();
    yes.disabled = !name;
    $("#presetWarn", sheet).textContent = S.presets.some(p => p.name === name)
      ? "Ein Preset mit diesem Namen gibt es schon — es wird überschrieben." : "";
  };
  const submit = async () => {
    const name = input.value.trim();
    if (!name) return;
    yes.disabled = true;
    const r = await api("save_preset", name, form);
    if (!r) { yes.disabled = false; return; }
    close();
    S.presets = r.presets; S.preset = r.name;
    renderPresets();
    toast(`Preset „${r.name}“ gespeichert.`, "ok");
  };
  input.oninput = check;
  input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } };
  $("[data-no]", sheet).onclick = close;
  yes.onclick = submit;
  check();
  input.focus();
}

async function deletePreset() {
  const name = S.preset;
  if (!name) return;
  const ok = await confirmBox({ title: "Preset löschen?", body: `„${name}“ wird endgültig entfernt.`,
                                ok: "Löschen", danger: true });
  if (!ok) return;
  const r = await api("delete_preset", name);
  if (!r) return;
  S.presets = r.presets; S.preset = "";
  renderPresets();
}

/* ---------- Ordner ---------- */
function applyFolder(r) {
  closeTrim();
  S.folder = r.folder;
  S.files = r.files || [];
  S.filter = "Alle"; S.sel.clear(); S.anchor = -1;
  S.phase = "idle"; S.outcome = null;
  $("#folderEmpty").hidden = true;
  $("#folderLoaded").hidden = false;
  $("#folderName").textContent = r.folder.name;
  $("#folderPath").textContent = r.folder.path;
  $("#folderPath").title = r.folder.path;
  $("#barFill").style.width = "0%"; $("#pct").textContent = "0%";
  renderFiles();
}

async function chooseFolder() {
  if (S.running) return;
  const r = await api("choose_folder");
  if (r && r.folder) applyFolder(r);
}

async function loadFolder(path) {
  const r = await api("load_folder", path);
  if (r) applyFolder(r);
}

/* Wird vom Backend aufgerufen, wenn etwas aufs Fenster gezogen wurde. */
window.onFolderDrop = (path) => {
  hideDrop();
  if (!path) { notice("Das war kein Ordner — bitte einen Ordner ablegen."); return; }
  if (S.running) { toast("Während der Konvertierung kann kein anderer Ordner geladen werden.", "err"); return; }
  loadFolder(path);
};

let dropTimer = null;
function showDrop() {
  $("#dropOverlay").hidden = false;
  clearTimeout(dropTimer);
  dropTimer = setTimeout(hideDrop, 400);   // falls kein dragleave mehr kommt
}
function hideDrop() { clearTimeout(dropTimer); $("#dropOverlay").hidden = true; }
const dragsFiles = (e) => Array.from(e.dataTransfer?.types || []).includes("Files");

function initDrop() {
  for (const type of ["dragenter", "dragover"]) {
    window.addEventListener(type, (e) => {
      if (!dragsFiles(e)) return;
      e.preventDefault();                  // sonst öffnet die WebView die Datei selbst
      e.dataTransfer.dropEffect = "copy";
      showDrop();
    });
  }
  window.addEventListener("dragleave", (e) => {
    if (e.clientX <= 0 || e.clientY <= 0 || e.clientX >= innerWidth || e.clientY >= innerHeight) hideDrop();
  });
  window.addEventListener("drop", (e) => { e.preventDefault(); hideDrop(); }, true);
}

/* ---------- Dateiliste ---------- */
function renderFiles() {
  const counts = {};
  for (const f of S.files) counts["." + f.ext] = (counts["." + f.ext] || 0) + 1;
  S.shown = S.filter === "Alle" ? S.files.slice() : S.files.filter(f => "." + f.ext === S.filter);

  $("#filesbar").hidden = !S.files.length;
  $("#pills").innerHTML = S.files.length
    ? ["Alle", ...Object.keys(counts).sort()].map(x =>
        `<button class="pill${x === S.filter ? " on" : ""}" data-filter="${esc(x)}">${esc(x)}<span>${x === "Alle" ? S.files.length : counts[x]}</span></button>`).join("")
    : "";

  const body = $("#tbody");
  if (!S.folder || !S.files.length) {
    body.innerHTML = S.folder
      ? `<div class="tempty"><div class="t1">Keine Mediendateien gefunden</div><div class="t2">In diesem Ordner und seinen Unterordnern liegen keine Videos, Audios oder Bilder.</div></div>`
      : `<div class="tempty"><div class="t1">Noch kein Ordner geladen</div><div class="t2">Ordner ins Fenster ziehen oder über „Ordner wählen“ öffnen. Gefunden werden Videos, Audios und Bilder.</div></div>`;
  } else {
    body.innerHTML = S.shown.map((f, i) => `<div class="grid trow${S.sel.has(f.rel) ? " on" : ""}" data-i="${i}">
      <div class="cb"><div class="box">${CHECK_SVG}</div></div>
      <div class="nm" title="${esc(f.rel)}"><span>${esc(f.dir)}</span>${esc(f.name)}</div>
      <div class="fmt">${KIND_SVG[f.kind] || ""}.${esc(f.ext)}</div>
    </div>`).join("");
  }
  body.scrollTop = 0;
  updateSel();
}

function paintRow(i) {
  const row = $("#tbody").children[i];
  if (row) row.classList.toggle("on", S.sel.has(S.shown[i].rel));
}

function setRange(a, b, on) {
  const [lo, hi] = a < b ? [a, b] : [b, a];
  for (let i = lo; i <= hi; i++) {
    const rel = S.shown[i]?.rel;
    if (rel === undefined) continue;
    if (on) S.sel.add(rel); else S.sel.delete(rel);
    paintRow(i);
  }
  updateSel();
}

function selectAll(on) {
  if (on) S.shown.forEach(f => S.sel.add(f.rel)); else S.sel.clear();
  S.shown.forEach((_, i) => paintRow(i));
  updateSel();
}

function selectedShown() { return S.shown.filter(f => S.sel.has(f.rel)); }

function updateSel() {
  const shown = S.shown.length, n = selectedShown().length;
  $("#countText").textContent = S.folder ? `${shown} angezeigt · ${n} ausgewählt` : "";
  const all = shown > 0 && n === shown, part = n > 0 && !all;
  $("#allBox").classList.toggle("on", all);
  $("#allBox").classList.toggle("part-on", part);
  $("#toggleAll").title = all ? "Auswahl aufheben" : "Alle auswählen";
  updateTrimButton();
  updateFooter();
}

/* Klick schaltet um, Shift-Klick schaltet den Bereich seit dem letzten Klick,
   Ziehen über Zeilen überträgt den Zustand der ersten Zeile (am Rand scrollt
   die Liste mit). */
function rowAt(y) {
  const body = $("#tbody"), r = body.getBoundingClientRect();
  const yy = Math.min(r.bottom - 2, Math.max(r.top + 2, y));
  const row = document.elementFromPoint(r.left + 30, yy)?.closest(".trow");
  return row ? parseInt(row.dataset.i, 10) : -1;
}

function dragMove(e) {
  const d = S.drag;
  if (!d) return;
  const r = $("#tbody").getBoundingClientRect();
  d.dir = e.clientY < r.top ? -1 : e.clientY > r.bottom ? 1 : 0;
  d.y = e.clientY;
  if (d.dir && !d.timer) d.timer = setInterval(dragScroll, 40);
  if (!d.dir) { clearInterval(d.timer); d.timer = null; }
  const i = rowAt(e.clientY);
  if (i >= 0 && i !== d.last) { setRange(d.last, i, d.on); d.last = i; }
}

function dragScroll() {
  const d = S.drag;
  if (!d || !d.dir) return;
  $("#tbody").scrollTop += d.dir * 22;
  const i = rowAt(d.y);
  if (i >= 0 && i !== d.last) { setRange(d.last, i, d.on); d.last = i; }
}

function dragEnd() {
  if (S.drag) clearInterval(S.drag.timer);
  S.drag = null;
  document.removeEventListener("mousemove", dragMove);
  document.removeEventListener("mouseup", dragEnd);
}

function initList() {
  const body = $("#tbody");
  body.addEventListener("mousedown", (e) => {
    const row = e.button === 0 && e.target.closest(".trow");
    if (!row) return;
    e.preventDefault();
    const i = parseInt(row.dataset.i, 10);
    const on = !S.sel.has(S.shown[i].rel);
    if (e.shiftKey && S.anchor >= 0) { setRange(S.anchor, i, on); S.anchor = i; return; }
    setRange(i, i, on);
    S.anchor = i;
    S.drag = { on, last: i, dir: 0, y: e.clientY, timer: null };
    document.addEventListener("mousemove", dragMove);
    document.addEventListener("mouseup", dragEnd);
  });
  body.addEventListener("dblclick", (e) => {
    const row = e.target.closest(".trow");
    if (row) api("open_file", S.shown[parseInt(row.dataset.i, 10)].rel);
  });
  $("#toggleAll").onclick = () => {
    if (!S.shown.length) return;
    selectAll(selectedShown().length !== S.shown.length);
  };
  $("#pills").onclick = (e) => {
    const b = e.target.closest(".pill");
    if (!b) return;
    S.filter = b.dataset.filter; S.sel.clear(); S.anchor = -1;
    renderFiles();
  };
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "a" && !typing && !closeSheet && !T.open && S.shown.length) {
      e.preventDefault(); selectAll(true);
    }
  });
}

/* ---------- Pipette „Aus Bild“ ---------- */
async function pickFromImage() {
  const img = selectedShown().find(f => f.kind === "image");
  if (!img) { notice("Zuerst ein Bild in der Liste auswählen, dann „Aus Bild“."); return; }
  const r = await api("picker_open", img.rel);
  if (!r) return;
  const { sheet, close } = openSheet(`
    <div class="sheet picker">
      <h3>Farbe wählen — ${esc(r.name)}</h3>
      <div class="imgbox"><img alt="" draggable="false"></div>
      <div class="readout">
        <div class="swatch" data-sw></div>
        <div class="t" data-rd>Über das Bild fahren — Klick übernimmt die Farbe</div>
        <button class="btn quiet mid" data-no>Abbrechen</button>
      </div>
    </div>`, () => api("picker_close"));
  const image = $("img", sheet), canvas = document.createElement("canvas");
  let ctx = null;
  image.onload = () => {
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(image, 0, 0);
  };
  image.src = r.src;
  const at = (e) => {
    const box = image.getBoundingClientRect();
    const fx = Math.min(0.9999, Math.max(0, (e.clientX - box.left) / box.width));
    const fy = Math.min(0.9999, Math.max(0, (e.clientY - box.top) / box.height));
    return { x: Math.floor(fx * r.width), y: Math.floor(fy * r.height), fx, fy };
  };
  image.onmousemove = (e) => {
    if (!ctx) return;
    const p = at(e);
    const [cr, cg, cb] = ctx.getImageData(Math.floor(p.fx * canvas.width), Math.floor(p.fy * canvas.height), 1, 1).data;
    const hex = "#" + [cr, cg, cb].map(v => v.toString(16).padStart(2, "0")).join("").toUpperCase();
    $("[data-sw]", sheet).style.background = hex;
    $("[data-rd]", sheet).textContent = `${hex}   bei (${p.x}, ${p.y})`;
  };
  let picked = false;
  image.onclick = async (e) => {
    if (picked) return;
    picked = true;
    const p = at(e);
    const res = await api("picker_pick", p.x, p.y);
    close();
    if (!res) return;
    $('input[data-k="img_bg_color"]').value = res.hex;
    setSeg("img_bg_mode", "color");
    syncForm();
  };
  $("[data-no]", sheet).onclick = () => sheet.dismiss();
}

function pickColor() {
  const field = $('input[data-k="img_bg_color"]'), native = $("#colorInput");
  const cur = field.value.trim();
  native.value = HEX_RE.test(cur) ? (cur.startsWith("#") ? cur : "#" + cur).toLowerCase() : "#ffffff";
  native.click();
}

/* ---------- Lauf ---------- */
async function start() {
  if (S.running || !S.files.length) return;
  const { form, bad } = readForm();
  if (bad.length) {
    notice("Bitte die Zahlenfelder prüfen — Auflösung, CRF, dB usw. müssen Zahlen sein.");
    bad[0].focus();
    return;
  }
  const rels = selectedShown().map(f => f.rel);
  if (!rels.length) { notice("Mindestens eine Datei auswählen (Kopfzeile anklicken für alle)."); return; }
  const r = await api("start", rels, form);
  if (!r) return;
  S.lastJob = r.job;
  if (S.finishedJob !== r.job) setRunning(true);
}

async function askStop() {
  if (!S.running) return;
  const ok = await confirmBox({
    title: "Konvertierung abbrechen?",
    body: "Der laufende Stapel wird gestoppt. Die gerade bearbeitete Datei ist danach eventuell unvollständig.",
    ok: "Abbrechen", cancel: "Weiter konvertieren", danger: true, tag: "stop",
  });
  if (!ok || !S.running) return;
  $("#btnStop").disabled = true;
  await api("cancel");
}

function setRunning(on) {
  S.running = on;
  $("#btnStop").disabled = !on;
  for (const id of ["btnUpdate", "btnPick", "btnBrowse"]) $("#" + id).disabled = on || (id === "btnUpdate" && S.updating);
  if (on) { S.phase = "idle"; $("#barFill").style.width = "0%"; $("#pct").textContent = "0%"; }
  document.body.classList.toggle("busy", on);
  updateTrimButton();
  updateFooter();
}

function finish(o) {
  setRunning(false);
  S.outcome = o;
  S.phase = o.state === "running" ? "idle" : o.state;
  if ($("#modal").dataset.tag === "stop") $(".sheet")?.dismiss();   // Lauf ist schon vorbei
  if (o.kind === "trim") trimResults(o);
  const failed = (o.total || 0) - (o.ok || 0), verb = o.kind === "trim" ? "getrimmt" : "konvertiert";
  if (o.state === "done") {
    toast(failed ? `${o.ok}/${o.total} ${verb} — ${failed} fehlgeschlagen (siehe Protokoll).`
                 : `${o.ok}/${o.total} ${verb}.`, failed ? "err" : "ok");
  } else if (o.state === "cancelled") {
    toast(`Abgebrochen — ${o.ok}/${o.total} erledigt.`);
  } else if (o.state === "failed") {
    toast(o.error || "Konvertierung fehlgeschlagen.", "err");
  }
  updateFooter();
}

function setBar(done, total) {
  const pct = total ? Math.round(done / total * 100) + "%" : "0%";
  $("#barFill").style.width = pct;
  $("#pct").textContent = pct;
}

function updateFooter(status) {
  const btn = $("#btnStart");
  if ($("#startLabel").textContent !== (T.open ? "Alle trimmen" : "Konvertierung starten")) {
    $("#startLabel").textContent = T.open ? "Alle trimmen" : "Konvertierung starten";
    $("#startIcon").outerHTML = T.open
      ? ICON.scissors.replace("<svg ", '<svg id="startIcon" ').replace(/width="16" height="16"/, 'width="19" height="19"').replace('stroke-width="2"', 'stroke-width="2.4"')
      : START_ICON;
  }
  btn.disabled = S.running || (T.open ? !T.clips.some(c => c.info) : !S.files.length);
  if (S.running) {
    if (status) { $("#state").textContent = status.title || "Läuft …"; $("#scope").textContent = status.text || ""; }
    return;
  }
  const n = selectedShown().length, o = S.outcome || {};
  // Ergebnis nur in der Ansicht zeigen, zu der der letzte Lauf gehört
  const phase = (o.kind === "trim") === T.open ? S.phase : "idle";
  let title = T.open ? "Trimmen" : "Bereit";
  let text = T.open ? trimSummary()
    : !S.folder ? "Kein Ordner geladen"
    : !S.files.length ? "Keine Mediendateien im Ordner"
    : `${n} von ${S.shown.length} ausgewählt → ${S.segs.target || ""}`;
  if (phase === "done") {
    title = "Fertig";
    text = T.open ? `${o.ok}/${o.total} getrimmt — gespeichert in „trim - converted“`
                  : `${o.ok}/${o.total} konvertiert — ${n} ausgewählt für den nächsten Lauf`;
  }
  if (phase === "cancelled") { title = "Abgebrochen"; text = `${o.ok}/${o.total} vor dem Stopp erledigt`; }
  if (phase === "failed") { title = "Fehlgeschlagen"; text = o.error || "Siehe Protokoll"; }
  $("#state").textContent = title;
  $("#scope").textContent = text;
  $("#scope").title = text;
}

/* ---------- Protokoll ---------- */
function logClass(t) {
  if (/^\s*✓|^✔/.test(t)) return "ok";
  if (/^\s*✗|^Abgebrochen|^Fehler|FEHLER/.test(t)) return "err";
  if (/^WARNUNG|ÜBERSPRUNGEN|übersprungen/.test(t)) return "warn";
  return "";
}

function addLog(lines) {
  const box = $("#log");
  const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const frag = document.createDocumentFragment();
  for (const t of lines) {
    const d = document.createElement("div");
    d.textContent = t === "" ? "​" : t;
    const c = logClass(t);
    if (c) d.className = c;
    frag.appendChild(d);
  }
  box.appendChild(frag);
  while (box.childElementCount > 5000) box.firstElementChild.remove();
  if (atEnd) box.scrollTop = box.scrollHeight;
}

async function tick() {
  let r = null;
  try { r = await window.pywebview.api.poll(); } catch (e) { r = null; }
  if (r && r.ok !== false) {
    if (r.lines?.length) addLog(r.lines);
    if (r.running) {
      if (!S.running) setRunning(true);
      setBar(r.progress?.done || 0, r.progress?.total || 0);
      updateFooter(r.status);
    } else if (S.running || (r.outcome?.id === S.lastJob && S.lastJob && S.phase === "idle" && r.outcome.state !== "running")) {
      setBar(r.progress?.done || 0, r.progress?.total || 0);
      S.lastJob = "";
      S.finishedJob = r.outcome?.id || "";
      finish(r.outcome || {});
    }
  }
  setTimeout(tick, 300);
}

/* ---------- Update ---------- */
function setUpdateLabel(text) { $("#updateLabel").textContent = text; }

async function checkUpdate() {
  if (S.updating || S.running) return;
  S.updating = true;
  $("#btnUpdate").disabled = true;
  setUpdateLabel("Suche …");
  const r = await api("check_update");
  S.updating = false;
  $("#btnUpdate").disabled = S.running;
  setUpdateLabel("Update suchen");
  if (!r) return;
  if (r.state === "nogit") {
    infoBox({ title: "Update suchen", body: "Diese Version ist noch nicht mit Git verbunden.\n\nFühre einmal den Installer aus (install.sh bzw. install.bat) — danach funktioniert die Update-Suche." });
    return;
  }
  if (r.state === "error") { infoBox({ title: "Update suchen", body: "Konnte nicht nach Updates suchen.", pre: r.message }); return; }
  if (r.state === "current") { toast("Die App ist auf dem neuesten Stand.", "ok"); return; }
  const word = r.behind === 1 ? "Änderung" : "Änderungen";
  const ok = await confirmBox({
    title: "Update verfügbar",
    body: `Eine neuere Version ist verfügbar (${r.behind} ${word}).\n\nJetzt herunterladen und die App neu starten?`,
    ok: "Aktualisieren", cancel: "Später",
  });
  if (!ok) return;
  S.updating = true;
  $("#btnUpdate").disabled = true;
  setUpdateLabel("Aktualisiere …");
  let up = null;
  try { up = await window.pywebview.api.apply_update(); } catch (e) { up = { ok: false, error: String(e) }; }
  if (!up || up.ok === false) {
    S.updating = false;
    $("#btnUpdate").disabled = S.running;
    setUpdateLabel("Update suchen");
    infoBox({ title: "Update fehlgeschlagen", pre: up?.error || "Unbekannter Fehler" });
    return;
  }
  setUpdateLabel("Neustart …");
  toast("Update geladen — die App startet neu …", "ok");
  setTimeout(() => api("restart"), 700);
}

/* ---------- Zieh-Griffe (Dateiliste / Protokoll) ---------- */
function initGrips() {
  $$(".grip").forEach(g => {
    const varName = g.dataset.grip, min = parseInt(g.dataset.min, 10);
    g.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const y0 = e.clientY;
      const h0 = parseInt(getComputedStyle(document.documentElement).getPropertyValue(varName), 10);
      const move = (ev) => document.documentElement.style.setProperty(
        varName, Math.max(min, h0 + ev.clientY - y0) + "px");
      const up = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
    g.addEventListener("dblclick", () => document.documentElement.style.removeProperty(varName));
  });
}

/* ---------- Start ---------- */
function initSettings() {
  const card = $(".settings");
  card.addEventListener("click", (e) => {
    const b = e.target.closest(".seg button");
    if (b) {
      const k = b.closest(".seg").dataset.k;
      setSeg(k, b.dataset.v);
      if (k === "target" && S.defaultCrf[b.dataset.v] !== undefined) {
        const crf = $('input[data-k="crf"]');
        crf.value = S.defaultCrf[b.dataset.v];
        validateField(crf);
      }
      syncForm();
      return;
    }
    const c = e.target.closest(".check[data-k]");
    if (c) { setCheck(c, !c.classList.contains("on")); syncForm(); }
  });
  card.addEventListener("input", (e) => {
    if (e.target.matches("input[data-k]")) { validateField(e.target); syncForm(); }
  });
  card.addEventListener("change", (e) => { if (e.target.matches("select[data-k]")) syncForm(); });
  $("#btnColor").onclick = pickColor;
  $("#colorInput").addEventListener("input", (e) => {
    const field = $('input[data-k="img_bg_color"]');
    field.value = e.target.value.toUpperCase();
    validateField(field);
    syncForm();
  });
  $("#btnFromImage").onclick = pickFromImage;
}

async function boot() {
  const st = await api("get_state");
  if (!st) return;
  S.defaults = st.defaults;
  S.defaultCrf = st.default_crf;
  S.formats = st.formats;
  S.presets = st.presets || [];

  $("#ffBadge").classList.add(st.ffmpeg ? "ok" : "bad");
  $("#ffVal").textContent = st.ffmpeg ? "gefunden" : "nicht gefunden";
  $("#ffBadge").title = st.ffmpeg ? st.ffmpeg_path : "brew install ffmpeg (macOS) bzw. winget install ffmpeg (Windows)";
  if (!st.realesrgan) {
    $("#esrganHint").textContent = "Real-ESRGAN · nicht gefunden";
    $("#esrganHint").classList.add("warn");
  }

  initSettings();
  writeForm(S.defaults);
  renderPresets();
  renderFiles();

  addLog(["Folder Converter bereit. Ordner ins Fenster ziehen oder „Ordner wählen“."]);
  if (!st.ffmpeg) {
    addLog(["WARNUNG: FFmpeg wurde nicht gefunden. Installieren mit 'brew install ffmpeg' (macOS), dann neu starten."]);
  }

  $("#preset").onchange = applyPreset;
  $("#btnSave").onclick = savePreset;
  $("#btnDelete").onclick = deletePreset;
  $("#btnPick").onclick = chooseFolder;
  $("#btnBrowse").onclick = chooseFolder;
  $("#btnUpdate").onclick = checkUpdate;
  $("#btnClearLog").onclick = () => { $("#log").innerHTML = ""; };
  $("#btnStart").onclick = () => (T.open ? trimAll() : start());
  $("#btnStop").onclick = askStop;

  initList();
  initTrim();
  initDrop();
  initGrips();
  if (st.running) setRunning(true);
  tick();
}

if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot);
