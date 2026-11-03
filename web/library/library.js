// Library page: list/search, play, rename, trash/restore, import. All text is set via textContent.
(function () {
  const $ = (id) => document.getElementById(id);
  const rows = $("rows"), statusEl = $("status"), q = $("q"), dlg = $("confirmDlg");
  let trashView = false, timer = null, seq = 0;

  function say(msg, bad) { statusEl.textContent = msg || ""; statusEl.className = bad ? "err" : ""; }
  function fmtDur(ms) {
    const s = Math.round(ms / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    const p = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${p(m)}:${p(x)}` : `${p(m)}:${p(x)}`;
  }
  function fmtSize(b) {
    if (b == null) return "";
    if (b >= 1e9) return (b / 1e9).toFixed(1) + " GB";
    if (b >= 1e6) return (b / 1e6).toFixed(1) + " MB";
    return Math.max(1, Math.round(b / 1e3)) + " KB";
  }
  async function api(path, opts) {
    const r = await fetch(path, opts);
    let j = null;
    try { j = await r.json(); } catch (e) { /* non-JSON error body */ }
    if (!r.ok) throw new Error((j && j.error) || `${r.status} ${r.statusText}`);
    return j;
  }
  function el(tag, props, text) {
    const e = document.createElement(tag);
    if (props) Object.assign(e, props);
    if (text != null) e.textContent = text;
    return e;
  }
  function cell(tr, text, cls, label) {
    const td = el("td", cls ? { className: cls } : null, text);
    if (label) td.dataset.label = label;
    tr.appendChild(td);
    return td;
  }

  function confirmTrash(title) {
    return new Promise((resolve) => {
      $("confirmMsg").textContent = `Move "${title}" to the trash? You can restore it for 30 days.`;
      const done = (v) => { dlg.close(); resolve(v); };
      $("confirmYes").onclick = () => done(true);
      $("confirmNo").onclick = () => done(false);
      dlg.oncancel = () => resolve(false);
      dlg.showModal();
    });
  }

  function startRename(r, td) {
    const inp = el("input", { type: "text", className: "rename", value: r.title, maxLength: 200 });
    inp.setAttribute("aria-label", "Recording title");
    let finished = false;
    const finish = async (save) => {
      if (finished) return;
      finished = true;
      if (save && inp.value.trim() && inp.value.trim() !== r.title) {
        try { await api(`/api/recordings/${r.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
                                                     body: JSON.stringify({ title: inp.value }) }); say("Renamed."); }
        catch (e) { say("Rename failed: " + e.message, true); }
      }
      load();
    };
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); else if (e.key === "Escape") finish(false); });
    inp.addEventListener("blur", () => finish(true));
    td.textContent = "";
    td.appendChild(inp);
    inp.focus();
    inp.select();
  }

  function addPlayer(r, td, btn) {
    if (td.querySelector("audio")) return;
    const a = el("audio", { controls: true, preload: "none", src: `/api/recordings/${r.id}/audio` });
    a.setAttribute("aria-label", "Play " + r.title);
    td.appendChild(a);
    btn.hidden = true;
    a.play().catch(() => {});
  }

  function render(list) {
    rows.textContent = "";
    $("empty").hidden = list.length > 0;
    $("tbl").hidden = list.length === 0;
    if (!list.length) {
      $("empty").textContent = "";
      if (trashView) $("empty").textContent = "The trash is empty.";
      else if (q.value.trim()) $("empty").textContent = "No recordings match your search.";
      else {
        $("empty").appendChild(document.createTextNode("No recordings yet. "));
        $("empty").appendChild(el("a", { href: "/record" }, "Record one"));
        $("empty").appendChild(document.createTextNode(" or import a file."));
      }
    }
    for (const r of list) {
      const tr = el("tr");
      const t = cell(tr, r.title, "title");
      cell(tr, new Date(r.started_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }), "when", "Date");
      cell(tr, fmtDur(r.duration_ms), "num", "Length");
      cell(tr, fmtSize(r.size_bytes), "num", "Size");
      cell(tr, String(r.runs_count), "num", "Runs");
      const act = el("td", { className: "actions" });
      if (trashView) {
        const b = el("button", { type: "button", className: "btn sm" }, "Restore");
        b.onclick = async () => {
          try { await api(`/api/recordings/${r.id}/restore`, { method: "POST" }); say("Restored."); load(); }
          catch (e) { say("Restore failed: " + e.message, true); }
        };
        act.appendChild(b);
      } else {
        const play = el("button", { type: "button", className: "btn sm" }, "Play");
        play.onclick = () => addPlayer(r, t, play);
        const ana = el("a", { className: "btn sm ghost", href: `/analyse?rec=${encodeURIComponent(r.id)}` }, "Analyse");
        const ren = el("button", { type: "button", className: "btn sm ghost" }, "Rename");
        ren.onclick = () => startRename(r, t);
        const del = el("button", { type: "button", className: "btn sm ghost" }, "Delete");
        del.onclick = async () => {
          if (!await confirmTrash(r.title)) return;
          try { await api(`/api/recordings/${r.id}`, { method: "DELETE" }); say("Moved to trash."); load(); }
          catch (e) { say("Delete failed: " + e.message, true); }
        };
        act.append(play, ana, ren, del);
      }
      tr.appendChild(act);
      rows.appendChild(tr);
    }
  }

  async function load() {
    const my = ++seq;
    const p = new URLSearchParams();
    if (q.value.trim()) p.set("q", q.value.trim());
    if (trashView) p.set("trash", "1");
    try {
      const list = await api("/api/recordings?" + p);
      if (my === seq) render(list);
    } catch (e) { say("Could not load the library: " + e.message, true); }
  }

  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 200); });
  $("trashToggle").onclick = () => {
    trashView = !trashView;
    $("trashToggle").setAttribute("aria-pressed", String(trashView));
    $("trashToggle").textContent = trashView ? "Back to library" : "Trash";
    $("listTitle").textContent = trashView ? "Trash" : "Recordings";
    load();
  };

  function importFile(f) {
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f, f.name);
    const x = new XMLHttpRequest();
    $("importBtn").disabled = true;
    say("Importing... uploading 0%");
    x.upload.onprogress = (e) => {
      if (e.lengthComputable) say(e.loaded >= e.total ? "Importing... converting (long files take a while)"
                                                      : `Importing... uploading ${Math.round(100 * e.loaded / e.total)}%`);
    };
    x.onload = () => {
      $("importBtn").disabled = false;
      let j = null;
      try { j = JSON.parse(x.responseText); } catch (e) { /* not JSON */ }
      if (x.status === 201) {
        say(`Imported "${j.title}".`);
        if (trashView) $("trashToggle").click(); else load();
      } else say("Import failed: " + ((j && j.error) || `${x.status} ${x.statusText}`), true);
    };
    x.onerror = () => { $("importBtn").disabled = false; say("Import failed: connection lost.", true); };
    x.open("POST", "/api/recordings/import");
    x.send(fd);
  }
  $("importBtn").onclick = () => $("file").click();
  $("file").onchange = () => { importFile($("file").files[0]); $("file").value = ""; };
  const drop = $("drop");
  for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
  for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); });
  drop.addEventListener("drop", (e) => importFile(e.dataTransfer.files[0]));

  load();
})();
