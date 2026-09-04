const state = {
  runtime: null,
  posts: [],
  voices: [],
  currentPost: null,
  page: 1,
  pages: 1,
  search: "",
  poll: null,
  compare: [], // up to 2 pinned generation ids, for quick A/B listening
};

const player = {
  audio: null,
  meta: null, // { id, kind: "generation" | "voice", url, title, download }
  speeds: [1, 1.25, 1.5, 1.75, 0.75],
  speedIndex: 0,
  waveCache: new Map(),
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function esc(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function cssEscape(value) {
  return window.CSS && CSS.escape ? CSS.escape(String(value)) : String(value).replace(/["\\]/g, "\\$&");
}

function short(value = "") {
  const text = String(value);
  return text.length > 22 ? `${text.slice(0, 11)}…${text.slice(-6)}` : text;
}

function date(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(parsed);
}

function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function skeletonCards(count) {
  return Array.from({ length: count }, () => `<div class="skeleton-card"></div>`).join("");
}

function toast(message, kind = "") {
  const node = $("#toast");
  node.textContent = message;
  node.className = `toast ${kind}`.trim();
  node.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.add("hidden"), 3500);
}

async function api(path, options = {}) {
  const init = {
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options,
  };
  if (init.body && typeof init.body !== "string") {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(init.body);
  }

  const response = await fetch(path, init);
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("application/json") ? await response.json() : {};

  if (response.status === 401) {
    showLogin();
    throw new Error("Studio session expired.");
  }
  if (!response.ok) {
    throw new Error(payload.message || payload.error || `Request failed (${response.status})`);
  }
  return payload;
}

function showLogin() {
  stopPoll();
  closePlayer();
  $("#studio-shell").classList.add("hidden");
  $("#login-view").classList.remove("hidden");
  setTimeout(() => $("#login-code").focus(), 10);
}

function showStudio() {
  $("#login-view").classList.add("hidden");
  $("#studio-shell").classList.remove("hidden");
}

function setView(name) {
  $$(".view").forEach((node) => node.classList.add("hidden"));
  $(`#${name}-view`).classList.remove("hidden");
  $$(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.view === name));

  if (name === "posts") {
    $("#crumb").textContent = "Ghost content";
    $("#page-title").textContent = "Published posts";
    stopPoll();
  } else if (name === "voices") {
    $("#crumb").textContent = "Voice library";
    $("#page-title").textContent = "Voices";
    stopPoll();
  } else {
    $("#crumb").textContent = "Post workspace";
    $("#page-title").textContent = state.currentPost?.post?.title || "Narration";
  }
}

function pill(value) {
  const text = value || "NOT QUEUED";
  const css = String(text).toLowerCase().replaceAll("_", "-").replaceAll(" ", "-");
  return `<span class="pill ${esc(css)}">${esc(text)}</span>`;
}

function updateRuntime() {
  const enabled = Boolean(state.runtime?.execution_enabled);
  const chips = [$("#runtime-chip"), $("#post-runtime")].filter(Boolean);

  chips.forEach((chip) => {
    chip.querySelector(".dot").classList.toggle("paused", !enabled);
    chip.querySelector("strong").textContent = enabled ? "Studio ready" : "Processing unavailable";
    chip.querySelector("small").textContent = enabled
      ? "Narration processing"
      : "Please refresh in a moment";
  });

  const activeVoices = state.voices.filter((voice) => voice.status === "ACTIVE");
  const button = $("#generate-button");
  if (button) {
    button.disabled = !enabled || activeVoices.length === 0;
    button.textContent = enabled ? "Generate audio" : "Processing unavailable";
  }

  const note = $("#execution-note");
  if (note) {
    note.querySelector(".dot").classList.toggle("paused", !enabled);
    note.querySelector("strong").textContent = enabled
      ? "Create a fresh narration."
      : "Narration processing is temporarily unavailable.";
    note.querySelector("p").textContent = enabled
      ? "Your voice and quote choices stay attached to this audio version."
      : "Refresh the Studio before trying again.";
  }
}

function postCard(post) {
  return `
    <article class="post-card" data-post-id="${esc(post.id)}">
      <div class="eyebrow">${esc(date(post.published_at))}</div>
      <h3>${esc(post.title || "Untitled post")}</h3>
      <p>${esc(post.excerpt || "Published Ghost story")}</p>
      <div class="post-card-foot">
        <span>${esc(post.primary_author || "Gratefulness")}</span>
        <span class="state-mini">${esc(post.studio_state || "Not generated")}</span>
      </div>
    </article>`;
}

async function loadPosts(page = 1) {
  const query = new URLSearchParams({ page: String(page), limit: "24" });
  if (state.search) query.set("search", state.search);
  const payload = await api(`/studio-api/posts?${query}`);
  state.posts = payload.items || [];
  state.page = payload.page || 1;
  state.pages = payload.pages || 1;

  $("#post-grid").innerHTML = state.posts.length
    ? state.posts.map(postCard).join("")
    : `<div class="empty"><strong>No posts found</strong>Try another search.</div>`;

  $("#post-count").textContent = `${payload.total || 0} published posts`;
  $("#page-label").textContent = `Page ${state.page} of ${state.pages}`;
  $("#prev-page").disabled = state.page <= 1;
  $("#next-page").disabled = state.page >= state.pages;
}

function voiceCard(voice) {
  return `
    <article class="voice-card">
      <div class="voice-icon">◖</div>
      <h3>${esc(voice.display_name || "Unnamed voice")}</h3>
      <div class="voice-id">${esc(voice.voice_id)}</div>
      <div class="voice-foot">
        ${pill(voice.status || "UNKNOWN")}
        <div class="voice-actions">
          <button class="play-button" data-voice-play="${esc(voice.voice_id)}">▶ Reference</button>
          ${
            voice.status === "ACTIVE"
              ? `<button class="play-button archive-button" data-voice-archive="${esc(voice.voice_id)}">Archive</button>`
              : ""
          }
        </div>
      </div>
    </article>`;
}

function renderVoices() {
  const active = state.voices.filter((voice) => voice.status === "ACTIVE");

  $("#voice-grid").innerHTML = active.length
    ? active.map(voiceCard).join("")
    : `<div class="empty"><strong>No active voices</strong>Add a reference WAV.</div>`;

  const options = active.map((voice) =>
    `<option value="${esc(voice.voice_id)}">${esc(voice.display_name || voice.voice_id)}</option>`
  ).join("");

  $("#narrator-select").innerHTML = options || `<option value="">No active voices</option>`;
  $("#quote-voice-select").innerHTML = `<option value="">Choose quote voice</option>${options}`;
  updateRuntime();
  syncPlayingHighlights();
}

async function loadVoices() {
  const payload = await api("/studio-api/voices");
  state.voices = payload.items || [];
  renderVoices();
}

function docBlock(block) {
  if (block.type === "heading") {
    return `<h3 class="doc-block heading">${esc(block.text || "")}</h3>`;
  }
  if (block.type === "quote") {
    return `<blockquote class="doc-block quote">${esc(block.text || "")}${
      block.speaker ? `<cite>— ${esc(block.speaker)}</cite>` : ""
    }</blockquote>`;
  }
  if (block.type === "list") {
    const tag = block.ordered ? "ol" : "ul";
    return `<${tag} class="doc-block list">${(block.items || []).map((item) => `<li>${esc(item)}</li>`).join("")}</${tag}>`;
  }
  return `<p class="doc-block">${esc(block.text || "")}</p>`;
}

function generationCard(gen) {
  const execution = gen.generation_status || "NOT QUEUED";
  const review = gen.review_status || "UNREVIEWED";
  const completed = execution === "COMPLETED";
  const comparing = state.compare.includes(gen.generation_id);
  return `
    <article class="generation-card" data-generation-id="${esc(gen.generation_id)}">
      <div>
        <div class="generation-title">
          <strong>${esc(short(gen.generation_id))}</strong>
          ${pill(execution)}
          ${pill(review)}
        </div>
        <div class="generation-meta">
          <span>${esc(gen.voice_name || short(gen.voice_id || ""))}</span>
          <span>quotes ${esc(gen.quote_mode || "—")}</span>
          <span>${esc(date(gen.created_at))}</span>
        </div>
      </div>
      <div class="generation-actions">
        ${completed ? `<button class="small-button" data-audio="${esc(gen.generation_id)}">▶ Listen</button>` : ""}
        ${completed ? `<button class="small-button ${comparing ? "is-active" : ""}" data-compare="${esc(gen.generation_id)}">${comparing ? "✓ Comparing" : "Compare"}</button>` : ""}
        ${completed ? `<button class="small-button" data-review="SELECTED" data-gen="${esc(gen.generation_id)}">Select</button>` : ""}
        ${review === "SELECTED" ? `<button class="small-button" data-review="READY" data-gen="${esc(gen.generation_id)}">Ready</button>` : ""}
        ${completed ? `<button class="small-button" data-review="OUTDATED" data-gen="${esc(gen.generation_id)}">Outdate</button>` : ""}
      </div>
    </article>`;
}

function renderGenerations() {
  const generations = state.currentPost?.generations || [];
  $("#generation-count").textContent = `${generations.length} generation${generations.length === 1 ? "" : "s"}`;
  $("#generation-list").innerHTML = generations.length
    ? generations.map(generationCard).join("")
    : `<div class="empty"><strong>No audio yet</strong>Choose a voice when processing is enabled.</div>`;
  renderCompareTray();
  syncPlayingHighlights();
}

function renderCompareTray() {
  const tray = $("#compare-tray");
  if (!state.compare.length) {
    tray.classList.add("hidden");
    return;
  }
  tray.classList.remove("hidden");
  const generations = state.currentPost?.generations || [];
  const labels = ["A", "B"];
  $("#compare-chips").innerHTML = state.compare.map((id, index) => {
    const gen = generations.find((item) => item.generation_id === id);
    return `<span class="compare-chip" data-compare-play="${esc(id)}">${labels[index]} · ${esc(gen?.voice_name || short(id))}<button data-compare-remove="${esc(id)}" aria-label="Remove from comparison">×</button></span>`;
  }).join("");
}

function toggleCompare(generationId) {
  const index = state.compare.indexOf(generationId);
  if (index >= 0) {
    state.compare.splice(index, 1);
  } else {
    if (state.compare.length >= 2) state.compare.shift();
    state.compare.push(generationId);
  }
  renderGenerations();
}

function renderCurrentPost(payload) {
  state.currentPost = payload;
  const post = payload.post;
  const doc = payload.document;
  const generations = payload.generations || [];

  $("#post-meta").textContent = `${date(post.published_at)} · ${post.primary_author || "Gratefulness"}`;
  $("#post-title").textContent = post.title;
  $("#ghost-link").href = post.url;
  $("#page-title").textContent = post.title;
  $("#document-heading").textContent = `${doc.blocks.length} narration blocks`;
  $("#document-preview").innerHTML = doc.blocks.map(docBlock).join("");

  renderGenerations();
  updateRuntime();

  const live = generations.some((gen) => ["QUEUED", "RUNNING"].includes(gen.generation_status));
  if (live) startPoll(post.id);
  else stopPoll();
}

async function openPost(postId) {
  state.compare = [];
  const payload = await api(`/studio-api/posts/${encodeURIComponent(postId)}`);
  renderCurrentPost(payload);
  setView("post");
  history.replaceState({ postId }, "", `/studio?post=${encodeURIComponent(postId)}`);
}

async function refreshPost() {
  const postId = state.currentPost?.post?.id;
  if (!postId) return;
  const payload = await api(`/studio-api/posts/${encodeURIComponent(postId)}`);
  renderCurrentPost(payload);
}

function startPoll(postId) {
  stopPoll();
  state.poll = setInterval(async () => {
    if (state.currentPost?.post?.id !== postId || $("#post-view").classList.contains("hidden")) {
      stopPoll();
      return;
    }
    try { await refreshPost(); } catch {}
  }, 7000);
}

function stopPoll() {
  if (state.poll) clearInterval(state.poll);
  state.poll = null;
}

async function createGeneration() {
  if (!state.runtime?.execution_enabled) {
    throw new Error("Processing is currently paused.");
  }
  const postId = state.currentPost?.post?.id;
  const voiceId = $("#narrator-select").value;
  const quoteMode = $("#quote-mode").value;
  const quoteVoiceId = $("#quote-voice-select").value;

  if (!postId || !voiceId) throw new Error("Choose an active narrator.");

  const body = { voice_id: voiceId, quote_mode: quoteMode };
  if (quoteMode === "two_voice") {
    if (!quoteVoiceId) throw new Error("Choose a quote voice.");
    body.quote_voice_id = quoteVoiceId;
  }

  const prepared = await api(`/studio-api/posts/${encodeURIComponent(postId)}/generations`, {
    method: "POST",
    body,
  });

  const queued = await api(prepared.enqueue_path, { method: "POST", body: {} });
  toast(queued.already_queued ? "Generation was already queued." : "Generation queued.");
  await refreshPost();
}

async function reviewGeneration(generationId, reviewStatus) {
  const postId = state.currentPost?.post?.id;
  if (!postId) return;
  await api(
    `/studio-api/posts/${encodeURIComponent(postId)}/generations/${encodeURIComponent(generationId)}/review`,
    { method: "POST", body: { review_status: reviewStatus } },
  );
  await refreshPost();
  toast(`Marked ${reviewStatus}.`);
}

async function createVoice(name, file) {
  if (!file) throw new Error("Choose a WAV reference.");
  if (file.size > 10 * 1024 * 1024) throw new Error("WAV must be 10 MB or smaller.");

  const encoded = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read WAV."));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.readAsDataURL(file);
  });

  await api("/studio-api/voices", {
    method: "POST",
    body: { display_name: name, wav_base64: encoded },
  });
  await loadVoices();
  toast("New immutable voice created.");
}

async function loadRuntime() {
  state.runtime = await api("/studio-api/runtime");
  updateRuntime();
}

/* ==========================================================================
   Player — a persistent "now playing" dock instead of a fire-and-forget
   `new Audio().play()`. Handles generations and voice references alike.
   ========================================================================== */

function initPlayer() {
  player.audio = $("#player-audio");
  player.audio.addEventListener("timeupdate", updatePlayerProgress);
  player.audio.addEventListener("loadedmetadata", updatePlayerProgress);
  player.audio.addEventListener("play", () => setPlayerPlayingUI(true));
  player.audio.addEventListener("pause", () => setPlayerPlayingUI(false));
  player.audio.addEventListener("ended", () => setPlayerPlayingUI(false));
}

// Deterministic pseudo-waveform from the id, so the same take always draws
// the same shape. Not decoded audio amplitude — that would need to fetch
// and decode the full file client-side, which is fragile against signed
// URLs with no CORS headers. Cheap, stable, and reads as a real waveform.
function waveformBars(id) {
  if (player.waveCache.has(id)) return player.waveCache.get(id);
  let seed = 0;
  for (const ch of String(id)) seed = (seed * 31 + ch.charCodeAt(0)) >>> 0;
  const count = 64;
  const bars = [];
  for (let i = 0; i < count; i += 1) {
    seed = (seed * 1103515245 + 12345) >>> 0;
    const noise = (seed % 1000) / 1000;
    const envelope = 0.3 + 0.7 * Math.sin((i / count) * Math.PI);
    bars.push(Math.max(0.12, Math.min(1, noise * 0.6 + envelope * 0.55)));
  }
  player.waveCache.set(id, bars);
  return bars;
}

function renderWaveform(id) {
  const html = waveformBars(id).map((h) => `<span class="wave-bar" style="height:${Math.round(h * 100)}%"></span>`).join("");
  $("#waveform-bg").innerHTML = html;
  $("#waveform-fg").innerHTML = html;
}

function updatePlayerProgress() {
  const a = player.audio;
  if (!a || !player.meta) return;
  const duration = a.duration || 0;
  const current = a.currentTime || 0;
  const pct = duration ? (current / duration) * 100 : 0;
  $("#waveform-fg").style.width = `${pct}%`;
  $("#player-waveform").setAttribute("aria-valuenow", String(Math.round(pct)));
  $("#player-current").textContent = formatTime(current);
  $("#player-duration").textContent = formatTime(duration);
}

function setPlayerPlayingUI(isPlaying) {
  const btn = $("#player-toggle");
  btn.textContent = isPlaying ? "❚❚" : "▶";
  btn.setAttribute("aria-label", isPlaying ? "Pause" : "Play");
  syncPlayingHighlights();
}

function syncPlayingHighlights() {
  $$(".generation-card").forEach((card) => card.classList.remove("is-playing"));
  $$(".play-button").forEach((btn) => btn.classList.remove("is-playing"));
  if (!player.meta || player.audio.paused) return;
  if (player.meta.kind === "generation") {
    $(`.generation-card[data-generation-id="${cssEscape(player.meta.id)}"]`)?.classList.add("is-playing");
  } else {
    $(`[data-voice-play="${cssEscape(player.meta.id)}"]`)?.classList.add("is-playing");
  }
}

async function loadIntoPlayer({ id, kind, url, title, download }) {
  const a = player.audio;
  const sameTrack = player.meta && player.meta.id === id && player.meta.kind === kind;
  player.meta = { id, kind, url, title, download };

  $("#player-bar").classList.remove("hidden");
  $("#player-kind").textContent = kind === "generation" ? "Generation" : "Voice reference";
  $("#player-title").textContent = title;
  $("#player-download").href = url;
  $("#player-download").setAttribute("download", download || "");
  renderWaveform(id);

  if (!sameTrack) {
    a.src = url;
    a.playbackRate = player.speeds[player.speedIndex];
  }
  try {
    await a.play();
  } catch {
    // Autoplay may be blocked outside a direct gesture; player stays visible, paused.
  }
  syncPlayingHighlights();
}

function togglePlayerPlay() {
  const a = player.audio;
  if (!a.src) return;
  if (a.paused) a.play().catch(() => {}); else a.pause();
}

function seekPlayerBy(delta) {
  const a = player.audio;
  if (!a.src || !Number.isFinite(a.duration)) return;
  a.currentTime = Math.min(Math.max(0, a.currentTime + delta), a.duration);
}

function seekPlayerToRatio(ratio) {
  const a = player.audio;
  if (!a.src || !Number.isFinite(a.duration)) return;
  a.currentTime = Math.max(0, Math.min(1, ratio)) * a.duration;
}

function cyclePlayerSpeed() {
  player.speedIndex = (player.speedIndex + 1) % player.speeds.length;
  const rate = player.speeds[player.speedIndex];
  if (player.audio.src) player.audio.playbackRate = rate;
  $("#player-speed").textContent = `${rate}×`;
}

function setPlayerVolume(ratio) {
  player.audio.volume = ratio;
  player.audio.muted = ratio === 0;
  $("#player-mute").classList.toggle("is-muted", ratio === 0);
}

function togglePlayerMute() {
  const a = player.audio;
  a.muted = !a.muted;
  $("#player-mute").classList.toggle("is-muted", a.muted);
}

function closePlayer() {
  const a = player.audio;
  if (!a) return;
  a.pause();
  a.removeAttribute("src");
  a.load();
  player.meta = null;
  $("#player-bar")?.classList.add("hidden");
  syncPlayingHighlights();
}

async function playGeneration(generationId) {
  const postId = state.currentPost?.post?.id;
  const payload = await api(
    `/studio-api/posts/${encodeURIComponent(postId)}/generations/${encodeURIComponent(generationId)}/audio`,
  );
  const gen = (state.currentPost?.generations || []).find((item) => item.generation_id === generationId);
  const title = state.currentPost?.post?.title || "Narration";
  await loadIntoPlayer({
    id: generationId,
    kind: "generation",
    url: payload.url,
    title: `${title} — ${gen?.voice_name || short(generationId)}`,
    download: `${short(generationId)}.wav`,
  });
  history.replaceState({}, "", `/studio?post=${encodeURIComponent(postId)}&gen=${encodeURIComponent(generationId)}`);
}

async function playVoice(voiceId) {
  const payload = await api(`/studio-api/voices/${encodeURIComponent(voiceId)}/audio`);
  const voice = state.voices.find((item) => item.voice_id === voiceId);
  await loadIntoPlayer({
    id: voiceId,
    kind: "voice",
    url: payload.url,
    title: voice?.display_name || voiceId,
    download: `${voice?.display_name || voiceId}.wav`,
  });
}

function bindPlayer() {
  $("#player-toggle").addEventListener("click", togglePlayerPlay);
  $("#player-back").addEventListener("click", () => seekPlayerBy(-10));
  $("#player-forward").addEventListener("click", () => seekPlayerBy(10));
  $("#player-speed").addEventListener("click", cyclePlayerSpeed);
  $("#player-mute").addEventListener("click", togglePlayerMute);
  $("#player-close").addEventListener("click", closePlayer);

  $("#player-volume").addEventListener("input", (event) => {
    setPlayerVolume(Number(event.target.value) / 100);
  });

  $("#player-link").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      toast("Link to this take copied.");
    } catch {
      toast("Could not copy link.", "error");
    }
  });

  const waveEl = $("#player-waveform");
  const seekFromPointer = (event) => {
    const rect = waveEl.getBoundingClientRect();
    seekPlayerToRatio((event.clientX - rect.left) / rect.width);
  };
  waveEl.addEventListener("click", seekFromPointer);
  waveEl.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") { seekPlayerBy(5); event.preventDefault(); }
    else if (event.key === "ArrowLeft") { seekPlayerBy(-5); event.preventDefault(); }
  });

  document.addEventListener("keydown", (event) => {
    const tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea" || event.target.isContentEditable) return;
    if (!player.meta) return;

    if (event.code === "Space") { togglePlayerPlay(); event.preventDefault(); }
    else if (event.key === "ArrowRight") seekPlayerBy(10);
    else if (event.key === "ArrowLeft") seekPlayerBy(-10);
    else if (event.key === "ArrowUp") {
      player.audio.volume = Math.min(1, player.audio.volume + 0.1);
      $("#player-volume").value = String(Math.round(player.audio.volume * 100));
    } else if (event.key === "ArrowDown") {
      player.audio.volume = Math.max(0, player.audio.volume - 0.1);
      $("#player-volume").value = String(Math.round(player.audio.volume * 100));
    } else if (event.key.toLowerCase() === "m") {
      togglePlayerMute();
    }
  });
}

/* ---------- voice upload dropzone ---------- */
function bindDropzone() {
  const area = $("#dropzone-area");
  const input = $("#voice-file");
  const filenameEl = $("#dropzone-filename");
  const hintEl = $("#dropzone-hint");

  function showFile(file) {
    if (!file) {
      filenameEl.classList.add("hidden");
      hintEl.classList.remove("hidden");
      return;
    }
    filenameEl.textContent = `${file.name} · ${(file.size / (1024 * 1024)).toFixed(2)} MB`;
    filenameEl.classList.remove("hidden");
    hintEl.classList.add("hidden");
  }

  area.addEventListener("click", () => input.click());
  input.addEventListener("change", () => showFile(input.files[0]));

  ["dragenter", "dragover"].forEach((evt) => area.addEventListener(evt, (event) => {
    event.preventDefault();
    area.classList.add("drag-over");
  }));
  ["dragleave", "drop"].forEach((evt) => area.addEventListener(evt, (event) => {
    event.preventDefault();
    area.classList.remove("drag-over");
  }));
  area.addEventListener("drop", (event) => {
    const file = event.dataTransfer.files?.[0];
    if (!file) return;
    input.files = event.dataTransfer.files;
    showFile(file);
  });
}

function resetDropzone() {
  $("#dropzone-filename").classList.add("hidden");
  $("#dropzone-hint").classList.remove("hidden");
}

function bind() {
  initPlayer();
  bindPlayer();
  bindDropzone();

  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", async () => {
      const view = button.dataset.view;
      setView(view);
      history.replaceState({}, "", "/studio");
      if (view === "voices") await loadVoices();
    });
  });

  $("#back-button").addEventListener("click", () => {
    setView("posts");
    history.replaceState({}, "", "/studio");
  });

  $("#refresh-button").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.classList.add("spinning");
    try {
      await Promise.all([loadRuntime(), loadVoices()]);
      if (!$("#post-view").classList.contains("hidden")) await refreshPost();
      else await loadPosts(state.page);
      toast("Studio refreshed.");
    } catch (error) { toast(error.message, "error"); }
    finally { setTimeout(() => button.classList.remove("spinning"), 450); }
  });

  let searchTimer;
  $("#post-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      state.search = $("#post-search").value.trim();
      try { await loadPosts(1); } catch (error) { toast(error.message, "error"); }
    }, 250);
  });

  $("#prev-page").addEventListener("click", () => loadPosts(state.page - 1));
  $("#next-page").addEventListener("click", () => loadPosts(state.page + 1));
  $("#compare-clear").addEventListener("click", () => {
    state.compare = [];
    renderGenerations();
  });

  document.addEventListener("click", async (event) => {
    const post = event.target.closest("[data-post-id]");
    if (post) {
      try { await openPost(post.dataset.postId); } catch (error) { toast(error.message, "error"); }
      return;
    }

    const compareRemove = event.target.closest("[data-compare-remove]");
    if (compareRemove) {
      event.stopPropagation();
      state.compare = state.compare.filter((id) => id !== compareRemove.dataset.compareRemove);
      renderGenerations();
      return;
    }

    const comparePlay = event.target.closest("[data-compare-play]");
    if (comparePlay) {
      try { await playGeneration(comparePlay.dataset.comparePlay); } catch (error) { toast(error.message, "error"); }
      return;
    }

    const compareToggle = event.target.closest("[data-compare]");
    if (compareToggle) {
      toggleCompare(compareToggle.dataset.compare);
      return;
    }

    const review = event.target.closest("[data-review]");
    if (review) {
      try { await reviewGeneration(review.dataset.gen, review.dataset.review); } catch (error) { toast(error.message, "error"); }
      return;
    }

    const audio = event.target.closest("[data-audio]");
    if (audio) {
      try { await playGeneration(audio.dataset.audio); } catch (error) { toast(error.message, "error"); }
      return;
    }

    const archive = event.target.closest("[data-voice-archive]");
    if (archive) {
      const voiceId = archive.dataset.voiceArchive;
      const voice = state.voices.find((item) => item.voice_id === voiceId);
      const label = voice?.display_name || voiceId;

      if (!window.confirm(`Archive "${label}"? Existing generations stay pinned, but it cannot be used for new audio.`)) {
        return;
      }

      try {
        await api(`/studio-api/voices/${encodeURIComponent(voiceId)}/archive`, {
          method: "POST",
          body: {},
        });
        await loadVoices();
        toast("Voice archived. Existing generations are unchanged.");
      } catch (error) {
        toast(error.message, "error");
      }
      return;
    }

    const voice = event.target.closest("[data-voice-play]");
    if (voice) {
      try { await playVoice(voice.dataset.voicePlay); } catch (error) { toast(error.message, "error"); }
    }
  });

  $("#quote-mode").addEventListener("change", () => {
    $("#quote-voice-wrap").classList.toggle("hidden", $("#quote-mode").value !== "two_voice");
  });

  $("#generation-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try { await createGeneration(); } catch (error) { toast(error.message, "error"); }
  });

  $("#add-voice-button").addEventListener("click", () => {
    resetDropzone();
    $("#voice-dialog").showModal();
  });
  $("#voice-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await createVoice($("#voice-name").value.trim(), $("#voice-file").files[0]);
      $("#voice-dialog").close();
      event.target.reset();
      resetDropzone();
    } catch (error) { toast(error.message, "error"); }
  });

  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("#login-error").textContent = "";
    const response = await fetch("/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ code: $("#login-code").value }),
    });

    if (!response.ok) {
      $("#login-error").textContent = "Dashboard code was not accepted.";
      return;
    }

    $("#login-code").value = "";
    showStudio();
    try { await bootstrapData(); } catch (error) { toast(error.message, "error"); }
  });

  $("#logout-button").addEventListener("click", async () => {
    await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
    showLogin();
  });
}

async function restoreRoute() {
  const url = new URL(location.href);
  const postId = url.searchParams.get("post");
  const generationId = url.searchParams.get("gen");
  if (postId) {
    try {
      await openPost(postId);
      if (generationId) {
        try { await playGeneration(generationId); } catch (error) { toast(error.message, "error"); }
      }
      return;
    } catch (error) {
      toast(error.message, "error");
    }
  }
  setView("posts");
}

async function bootstrapData() {
  $("#post-grid").innerHTML = skeletonCards(6);
  $("#voice-grid").innerHTML = skeletonCards(3);
  await Promise.all([loadRuntime(), loadVoices(), loadPosts(1)]);
  await restoreRoute();
}

async function bootstrap() {
  bind();
  const session = await fetch("/auth/session", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });

  if (!session.ok) {
    showLogin();
    return;
  }

  showStudio();
  try { await bootstrapData(); } catch (error) { toast(error.message, "error"); }
}

bootstrap();
