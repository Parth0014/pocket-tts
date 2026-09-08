const R = {
  folders: [],
  folder: null,
  voices: [],
  search: "",
  sort: "name",
  mode: "create",
  editing: null,
};
let preview = null,
  playing = null;
const $ = (s, r = document) => r.querySelector(s),
  $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (v = "") =>
  String(v)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
function toast(m, k = "") {
  const n = $("#toast");
  if (!n) return;
  n.textContent = m;
  n.className = `toast ${k}`.trim();
  n.classList.remove("hidden");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => n.classList.add("hidden"), 3500);
}
async function api(path, o = {}) {
  const i = {
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(o.headers || {}) },
    ...o,
  };
  if (i.body && typeof i.body !== "string") {
    i.headers["Content-Type"] = "application/json";
    i.body = JSON.stringify(i.body);
  }
  const r = await fetch(path, i),
    t = r.headers.get("content-type") || "",
    p = t.includes("application/json") ? await r.json() : {};
  if (!r.ok)
    throw new Error(p.message || p.error || `Request failed (${r.status})`);
  return p;
}
const folderId = () => R.folder?.folder?.folder_id || null;
async function loadVoices() {
  const p = await api("/studio-api/voices");
  R.voices = (p.items || []).filter((v) => v.status === "ACTIVE");
}
async function loadFolders(preserve = true) {
  const prev = preserve ? folderId() : null,
    p = await api("/studio-api/reference-folders");
  R.folders = p.items || [];
  let next = prev;
  if (!next || !R.folders.some((f) => f.folder_id === next))
    next = R.folders[0]?.folder_id || null;
  renderFolders();
  if (next) await openFolder(next);
  else {
    R.folder = null;
    renderFolder();
  }
}
async function openFolder(id) {
  R.folder = await api(
    `/studio-api/reference-folders/${encodeURIComponent(id)}`,
  );
  renderFolders();
  renderFolder();
}
function renderFolders() {
  const t = $("#reference-folder-list");
  if (!t) return;
  if (!R.folders.length) {
    t.innerHTML =
      '<div class="reference-folder-empty"><strong>No folders yet</strong><span>Create one and add the voices you want to remember.</span></div>';
    return;
  }
  const sel = folderId();
  t.innerHTML = R.folders
    .map(
      (f) =>
        `<button type="button" class="reference-folder-row ${f.folder_id === sel ? "active" : ""}" data-reference-folder="${esc(f.folder_id)}"><span class="reference-folder-icon">⌑</span><span class="reference-folder-copy"><strong>${esc(f.folder_name)}</strong><small>${Number(f.reference_count || 0)} reference${Number(f.reference_count || 0) === 1 ? "" : "s"}</small></span><span class="reference-folder-chevron">›</span></button>`,
    )
    .join("");
}
const name = (x) => x.alias || x.voice_display_name || x.voice_id;
function refs() {
  let a = [...(R.folder?.references || [])],
    q = R.search.trim().toLowerCase();
  if (q)
    a = a.filter((x) =>
      [x.alias, x.short_description, x.voice_display_name, x.voice_id]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q)),
    );
  if (R.sort === "recent")
    a.sort((x, y) =>
      String(y.created_at || "").localeCompare(String(x.created_at || "")),
    );
  else if (R.sort === "favorite")
    a.sort((x, y) =>
      Boolean(x.favorite) !== Boolean(y.favorite)
        ? x.favorite
          ? -1
          : 1
        : name(x).localeCompare(name(y), undefined, { sensitivity: "base" }),
    );
  else
    a.sort((x, y) =>
      name(x).localeCompare(name(y), undefined, { sensitivity: "base" }),
    );
  return a;
}
function card(x) {
  const title = name(x),
    orig = x.alias
      ? `<span class="reference-original">${esc(x.voice_display_name || x.voice_id)}</span>`
      : "",
    desc = x.short_description ? `<p>${esc(x.short_description)}</p>` : "",
    bad = x.voice_status !== "ACTIVE",
    playingNow = playing === x.voice_id;
  return `<article class="reference-audio-card ${bad ? "is-unavailable" : ""}"><div class="reference-audio-main"><button class="reference-preview-button ${playingNow ? "is-playing" : ""}" type="button" data-reference-play="${esc(x.voice_id)}" aria-label="${playingNow ? "Pause" : "Play"} ${esc(title)}" ${bad ? "disabled" : ""}><svg class="icon" aria-hidden="true"><use href="#icon-${playingNow ? "pause" : "play"}"></use></svg></button><div class="reference-audio-copy"><div class="reference-audio-title-row"><h4>${esc(title)}</h4>${orig}</div>${desc}${bad ? '<span class="reference-warning">Voice is no longer active</span>' : ""}</div><button type="button" class="reference-favorite ${x.favorite ? "active" : ""}" data-reference-favorite="${esc(x.voice_id)}" aria-label="${x.favorite ? "Remove from" : "Add to"} favorites">${x.favorite ? "♥" : "♡"}</button></div><div class="reference-audio-actions"><button type="button" class="small-button reference-action-primary" data-voice-generations="${esc(x.voice_id)}">View generations</button><button type="button" class="small-button" data-reference-edit="${esc(x.voice_id)}">Edit details</button><button type="button" class="small-button danger-soft" data-reference-remove="${esc(x.voice_id)}">Remove</button></div></article>`;
}
function renderFolder() {
  const t = $("#reference-folder-title"),
    c = $("#reference-folder-count"),
    g = $("#reference-audio-grid"),
    a = $("#reference-folder-actions"),
    s = $("#reference-search");
  if (!t || !c || !g || !a || !s) return;
  if (!R.folder) {
    t.textContent = "Choose a folder";
    c.textContent = "Create a folder to start collecting reference voices.";
    a.classList.add("hidden");
    s.disabled = true;
    g.innerHTML =
      '<div class="reference-library-placeholder"><strong>Your saved reference voices will appear here.</strong><span>Alias and description are always optional.</span></div>';
    return;
  }
  const f = R.folder.folder,
    items = refs();
  t.textContent = f.folder_name;
  c.textContent = `${f.reference_count || 0} saved reference${Number(f.reference_count || 0) === 1 ? "" : "s"}`;
  a.classList.remove("hidden");
  s.disabled = false;
  g.innerHTML = items.length
    ? items.map(card).join("")
    : R.search
      ? '<div class="reference-library-placeholder"><strong>No matching references</strong><span>Try another search.</span></div>'
      : '<div class="reference-library-placeholder"><strong>This folder is empty</strong><span>Add any active voices. No alias or description is required.</span></div>';
}
async function refresh() {
  await Promise.all([loadVoices(), loadFolders()]);
}
function folderDialog(mode = "create") {
  R.mode = mode;
  const d = $("#reference-folder-dialog"),
    h = $("#reference-folder-dialog-title"),
    i = $("#reference-folder-name");
  h.textContent = mode === "rename" ? "Rename folder" : "Create folder";
  i.value = mode === "rename" ? R.folder?.folder?.folder_name || "" : "";
  d.showModal();
  setTimeout(() => i.focus(), 10);
}
function addDialog() {
  const d = $("#reference-add-dialog"),
    l = $("#reference-add-list"),
    existing = new Set((R.folder?.references || []).map((x) => x.voice_id)),
    available = R.voices.filter((v) => !existing.has(v.voice_id));
  if (!available.length) {
    toast("Every active voice is already in this folder.");
    return;
  }
  l.innerHTML = available
    .map(
      (v) =>
        `<label class="reference-voice-option"><input type="checkbox" value="${esc(v.voice_id)}"><span><strong>${esc(v.display_name || v.voice_id)}</strong><small>${esc(v.voice_id)}</small></span></label>`,
    )
    .join("");
  selectedCount();
  d.showModal();
}
function selectedCount() {
  const n = $$('#reference-add-list input[type="checkbox"]:checked').length,
    b = $("#reference-add-submit");
  b.disabled = n === 0;
  b.textContent = n
    ? `Add ${n} voice${n === 1 ? "" : "s"}`
    : "Add selected voices";
}
function editDialog(id) {
  const x = (R.folder?.references || []).find((r) => r.voice_id === id);
  if (!x) return;
  R.editing = x;
  $("#reference-meta-heading").textContent = x.voice_display_name || x.voice_id;
  $("#reference-alias").value = x.alias || "";
  $("#reference-description").value = x.short_description || "";
  $("#reference-favorite-checkbox").checked = Boolean(x.favorite);
  $("#reference-meta-dialog").showModal();
}
async function play(id) {
  if (playing === id && preview && !preview.paused) {
    preview.pause();
    playing = null;
    renderFolder();
    return;
  }
  const p = await api(`/studio-api/voices/${encodeURIComponent(id)}/audio`);
  if (preview) preview.pause();
  preview = new Audio(p.url);
  playing = id;
  preview.addEventListener("ended", () => {
    playing = null;
    renderFolder();
  });
  await preview.play();
  renderFolder();
}
async function favorite(id) {
  const x = (R.folder?.references || []).find((r) => r.voice_id === id);
  if (!x) return;
  R.folder = await api(
    `/studio-api/reference-folders/${encodeURIComponent(folderId())}/references/${encodeURIComponent(id)}`,
    { method: "PATCH", body: { favorite: !Boolean(x.favorite) } },
  );
  renderFolder();
  await loadFolders();
}
async function removeRef(id) {
  const x = (R.folder?.references || []).find((r) => r.voice_id === id);
  if (!x || !confirm(`Remove "${name(x)}" from this folder?`)) return;
  const f = folderId();
  await api(
    `/studio-api/reference-folders/${encodeURIComponent(f)}/references/${encodeURIComponent(id)}/remove`,
    { method: "POST", body: {} },
  );
  await openFolder(f);
  await loadFolders();
  toast("Reference removed from folder.");
}
async function archive() {
  const f = R.folder?.folder;
  if (
    !f ||
    !confirm(
      `Archive the folder "${f.folder_name}"? Saved audio files are not deleted.`,
    )
  )
    return;
  await api(
    `/studio-api/reference-folders/${encodeURIComponent(f.folder_id)}/archive`,
    { method: "POST", body: {} },
  );
  R.folder = null;
  await loadFolders(false);
  toast("Folder archived.");
}
function bind() {
  $("#reference-create-folder").addEventListener("click", () =>
    folderDialog("create"),
  );
  $("#reference-rename-folder").addEventListener("click", () =>
    folderDialog("rename"),
  );
  $("#reference-archive-folder").addEventListener("click", () =>
    archive().catch((e) => toast(e.message, "error")),
  );
  $("#reference-add-voices").addEventListener("click", addDialog);
  $("#reference-search").addEventListener("input", (e) => {
    R.search = e.target.value;
    renderFolder();
  });
  $("#reference-sort").addEventListener("change", (e) => {
    R.sort = e.target.value;
    renderFolder();
  });
  $("#reference-folder-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-reference-folder]");
    if (!b) return;
    R.search = "";
    $("#reference-search").value = "";
    openFolder(b.dataset.referenceFolder).catch((x) =>
      toast(x.message, "error"),
    );
  });
  $("#reference-audio-grid").addEventListener("click", (e) => {
    const p = e.target.closest("[data-reference-play]"),
      f = e.target.closest("[data-reference-favorite]"),
      d = e.target.closest("[data-reference-edit]"),
      r = e.target.closest("[data-reference-remove]");
    if (p)
      play(p.dataset.referencePlay).catch((x) => toast(x.message, "error"));
    else if (f)
      favorite(f.dataset.referenceFavorite).catch((x) =>
        toast(x.message, "error"),
      );
    else if (d) editDialog(d.dataset.referenceEdit);
    else if (r)
      removeRef(r.dataset.referenceRemove).catch((x) =>
        toast(x.message, "error"),
      );
  });
  $("#reference-folder-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const n = $("#reference-folder-name").value.trim();
      if (R.mode === "rename") {
        R.folder = await api(
          `/studio-api/reference-folders/${encodeURIComponent(folderId())}`,
          { method: "PATCH", body: { folder_name: n } },
        );
        $("#reference-folder-dialog").close();
        await loadFolders();
        toast("Folder renamed.");
      } else {
        const c = await api("/studio-api/reference-folders", {
          method: "POST",
          body: { folder_name: n },
        });
        $("#reference-folder-dialog").close();
        await loadFolders(false);
        await openFolder(c.folder_id);
        toast("Reference folder created.");
      }
    } catch (x) {
      toast(x.message, "error");
    }
  });
  $("#reference-add-list").addEventListener("change", selectedCount);
  $("#reference-add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const ids = $$('#reference-add-list input[type="checkbox"]:checked').map(
      (i) => i.value,
    );
    if (!ids.length) return;
    try {
      R.folder = await api(
        `/studio-api/reference-folders/${encodeURIComponent(folderId())}/references`,
        { method: "POST", body: { voice_ids: ids } },
      );
      $("#reference-add-dialog").close();
      renderFolder();
      await loadFolders();
      toast(`${ids.length} voice${ids.length === 1 ? "" : "s"} added.`);
    } catch (x) {
      toast(x.message, "error");
    }
  });
  $("#reference-meta-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const x = R.editing;
    if (!x) return;
    try {
      R.folder = await api(
        `/studio-api/reference-folders/${encodeURIComponent(folderId())}/references/${encodeURIComponent(x.voice_id)}`,
        {
          method: "PATCH",
          body: {
            alias: $("#reference-alias").value,
            short_description: $("#reference-description").value,
            favorite: $("#reference-favorite-checkbox").checked,
          },
        },
      );
      $("#reference-meta-dialog").close();
      R.editing = null;
      renderFolder();
      toast("Reference details saved.");
    } catch (y) {
      toast(y.message, "error");
    }
  });
  $$("[data-reference-close]").forEach((b) =>
    b.addEventListener("click", () => b.closest("dialog")?.close()),
  );
  const v = $("#references-view");
  new MutationObserver(() => {
    if (!v.classList.contains("hidden"))
      refresh().catch((e) => {
        if (!String(e.message).includes("401")) toast(e.message, "error");
      });
  }).observe(v, { attributes: true, attributeFilter: ["class"] });
  $$('[data-view="references"]').forEach((b) =>
    b.addEventListener("click", () =>
      setTimeout(() => refresh().catch((e) => toast(e.message, "error")), 0),
    ),
  );
  $("#refresh-button")?.addEventListener("click", () => {
    if (!v.classList.contains("hidden"))
      refresh().catch((e) => toast(e.message, "error"));
  });
}
bind();
