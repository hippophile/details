// Prompts page: list kinds and versions, show a version read-only, "Edit as new version" saves prompts/<kind>/vN.md.
// Text is only ever set via textContent. Pure helpers are exported for `node --test` (tests/js/prompts.test.mjs).
(function () {
  // ---- pure helpers -------------------------------------------------------------------------------------
  const CHECK_LABELS = { due_year_in_transcript: "year check" };
  const plural = (n, one) => `${n} ${n === 1 ? one : one + "s"}`;
  function versionLabel(v, latest) { return `v${v.version}` + (v.version === latest ? " (latest)" : "") + ` · ${plural(v.runs || 0, "run")}`; }
  function checksText(checks) { return (checks || []).map((c) => CHECK_LABELS[c] || c).join(" + "); }
  function canSave(s) { return !s.busy && s.body.trim() !== "" && s.body !== s.base; }
  function savePayload(baseVersion, body, note) {
    const p = { base_version: baseVersion, body };
    if (note && note.trim()) p.note = note.trim();
    return p;
  }
  function resultText(r) { return `Saved ${r.kind} v${r.version} as ${r.path}. Commit this file to git so your other machines get it.`; }
  const exported = { versionLabel, checksText, canSave, savePayload, resultText };
  if (typeof module !== "undefined" && module.exports) { module.exports = exported; return; }

  // ---- page ---------------------------------------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const TITLES = { brief: "Brief", debrief: "Debrief", notes: "Notes", checklist: "Checklist", tags: "Tags" };
  let kinds = [], kind = "", shown = null, busy = false;

  function el(tag, props, text) {
    const e = document.createElement(tag);
    if (props) Object.assign(e, props);
    if (text != null) e.textContent = text;
    return e;
  }
  async function api(path, opts) {
    const r = await fetch(path, opts);
    let j = null;
    try { j = await r.json(); } catch (e) { /* non-JSON body */ }
    if (!r.ok) throw new Error((j && j.error) || `${r.status} ${r.statusText}`);
    return j;
  }
  function status(text, bad) { $("status").textContent = text || ""; $("status").classList.toggle("bad", !!bad); }

  async function load(selectKind, selectVersion) {
    try { kinds = (await api("/api/prompts")).kinds; } catch (e) { status("Could not load the prompts: " + e.message, true); return; }
    status("");
    const sel = $("kindList");
    sel.replaceChildren(...kinds.map((k) => el("option", { value: k.kind }, TITLES[k.kind] || k.kind)));
    kind = selectKind && kinds.some((k) => k.kind === selectKind) ? selectKind : (kinds[0] || {}).kind || "";
    sel.value = kind;
    renderVersions();
    const k = kinds.find((x) => x.kind === kind);
    if (k) await show(selectVersion || k.latest);
  }

  function renderVersions() {
    const k = kinds.find((x) => x.kind === kind);
    const ul = $("versionList");
    ul.replaceChildren();
    if (!k) return;
    for (const v of [...k.versions].reverse()) {
      const li = el("li");
      const b = el("button", { type: "button" });
      b.setAttribute("aria-pressed", String(!!shown && shown.version === v.version));
      b.dataset.version = String(v.version);
      b.appendChild(el("span", null, versionLabel(v, k.latest)));
      const c = checksText(v.checks);
      if (c) b.appendChild(el("span", { className: "chk" }, "+ " + c));
      b.appendChild(el("span", { className: "meta" }, new Date(v.created_at).toLocaleString()));
      b.onclick = () => show(v.version);
      li.appendChild(b);
      ul.appendChild(li);
    }
  }

  async function show(version) {
    let d;
    try { d = await api(`/api/prompts/${encodeURIComponent(kind)}/${version}`); } catch (e) { status("Could not load the version: " + e.message, true); return; }
    status("");
    shown = d;
    closeEditor();
    $("pTitle").textContent = `${TITLES[kind] || kind} v${d.version}` + (d.version === d.latest ? " (latest)" : "");
    const c = checksText(d.checks);
    $("pMeta").textContent = [c ? "Code check: " + c : "No code checks", d.note ? "Note: " + d.note : "", "sha256 " + d.sha256.slice(0, 12)].filter(Boolean).join(" · ");
    $("pBody").textContent = d.body;
    $("editBtn").disabled = false;
    for (const b of $("versionList").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.version === String(d.version)));
  }

  function syncSave() { $("saveBtn").disabled = !canSave({ body: $("editText").value, base: shown ? shown.body : "", busy }); }
  function openEditor() {
    if (!shown) return;
    $("editText").value = shown.body;
    $("editNote").value = "";
    $("saveMsg").textContent = "";
    $("saveMsg").classList.remove("bad");
    $("editor").hidden = false;
    syncSave();
    $("editText").focus();
  }
  function closeEditor() { $("editor").hidden = true; }

  async function save() {
    busy = true;
    syncSave();
    $("saveMsg").classList.remove("bad");
    $("saveMsg").textContent = "Saving…";
    try {
      const r = await api(`/api/prompts/${encodeURIComponent(kind)}`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(savePayload(shown.version, $("editText").value, $("editNote").value)) });
      busy = false;
      await load(kind, r.version);
      $("result").textContent = resultText(r);
    } catch (e) {
      busy = false;
      $("saveMsg").textContent = e.message;       // the text stays in the box
      $("saveMsg").classList.add("bad");
      syncSave();
    }
  }

  $("kindList").onchange = async () => { kind = $("kindList").value; shown = null; renderVersions(); $("result").textContent = ""; const k = kinds.find((x) => x.kind === kind); if (k) await show(k.latest); };
  $("editBtn").onclick = openEditor;
  $("cancelBtn").onclick = closeEditor;
  $("editText").oninput = syncSave;
  $("saveBtn").onclick = save;
  load();
})();
