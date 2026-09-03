const state = {
  runtime: null,
  posts: [],
  voices: [],
  currentPost: null,
  page: 1,
  pages: 1,
  search: "",
  poll: null,
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
    $("#crumb").textContent = "GHOST CONTENT";
    $("#page-title").textContent = "Published posts";
    stopPoll();
  } else if (name === "voices") {
    $("#crumb").textContent = "VOICE LIBRARY";
    $("#page-title").textContent = "Voices";
    stopPoll();
  } else {
    $("#crumb").textContent = "POST WORKSPACE";
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
    chip.querySelector("strong").textContent = enabled ? "Processing online" : "Processing paused";
    chip.querySelector("small").textContent = enabled
      ? "Up to 6 narrations can run together"
      : "6 × 8 GB profile ready";
  });

  const button = $("#generate-button");
  if (button) {
    button.disabled = !enabled || state.voices.filter((voice) => voice.status === "ACTIVE").length === 0;
    button.textContent = enabled ? "Generate audio" : "Processing paused";
  }

  const note = $("#execution-note");
  if (note) {
    note.querySelector(".dot").classList.toggle("paused", !enabled);
    note.querySelector("strong").textContent = enabled
      ? "Generation will enqueue immediately."
      : "Execution is paused for frontend review.";
    note.querySelector("p").textContent = enabled
      ? "The job will enter the six-worker FIFO pipeline and status will update automatically."
      : "The six-worker runtime is configured, but no jobs can be queued from this UI yet.";
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
  $("#voice-grid").innerHTML = state.voices.length
    ? state.voices.map(voiceCard).join("")
    : `<div class="empty"><strong>No voices yet</strong>Add a reference WAV.</div>`;

  const active = state.voices.filter((voice) => voice.status === "ACTIVE");
  const options = active.map((voice) =>
    `<option value="${esc(voice.voice_id)}">${esc(voice.display_name || voice.voice_id)}</option>`
  ).join("");

  $("#narrator-select").innerHTML = options || `<option value="">No active voices</option>`;
  $("#quote-voice-select").innerHTML = `<option value="">Choose quote voice</option>${options}`;
  updateRuntime();
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
  return `
    <article class="generation-card">
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
        ${completed ? `<button class="small-button" data-review="SELECTED" data-gen="${esc(gen.generation_id)}">Select</button>` : ""}
        ${review === "SELECTED" ? `<button class="small-button" data-review="READY" data-gen="${esc(gen.generation_id)}">Ready</button>` : ""}
        ${completed ? `<button class="small-button" data-review="OUTDATED" data-gen="${esc(gen.generation_id)}">Outdate</button>` : ""}
      </div>
    </article>`;
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
  $("#document-heading").textContent = `Processor V${doc.processor_version} · ${doc.blocks.length} blocks`;
  $("#document-hashes").innerHTML = `content ${esc(short(doc.content_hash))}<br>narration ${esc(short(doc.narration_hash))}`;
  $("#document-preview").innerHTML = doc.blocks.map(docBlock).join("");
  $("#generation-count").textContent = `${generations.length} generation${generations.length === 1 ? "" : "s"}`;
  $("#generation-list").innerHTML = generations.length
    ? generations.map(generationCard).join("")
    : `<div class="empty"><strong>No audio yet</strong>Choose a voice when processing is enabled.</div>`;

  updateRuntime();
  const live = generations.some((gen) => ["QUEUED", "RUNNING"].includes(gen.generation_status));
  if (live) startPoll(post.id);
  else stopPoll();
}

async function openPost(postId) {
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

async function playGeneration(generationId) {
  const postId = state.currentPost?.post?.id;
  const payload = await api(
    `/studio-api/posts/${encodeURIComponent(postId)}/generations/${encodeURIComponent(generationId)}/audio`,
  );
  const audio = new Audio(payload.url);
  await audio.play();
}

async function playVoice(voiceId) {
  const payload = await api(`/studio-api/voices/${encodeURIComponent(voiceId)}/audio`);
  const audio = new Audio(payload.url);
  await audio.play();
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

function bind() {
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

  $("#refresh-button").addEventListener("click", async () => {
    try {
      await Promise.all([loadRuntime(), loadVoices()]);
      if (!$("#post-view").classList.contains("hidden")) await refreshPost();
      else await loadPosts(state.page);
      toast("Studio refreshed.");
    } catch (error) { toast(error.message, "error"); }
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

  document.addEventListener("click", async (event) => {
    const post = event.target.closest("[data-post-id]");
    if (post) {
      try { await openPost(post.dataset.postId); } catch (error) { toast(error.message, "error"); }
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

  $("#add-voice-button").addEventListener("click", () => $("#voice-dialog").showModal());
  $("#voice-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await createVoice($("#voice-name").value.trim(), $("#voice-file").files[0]);
      $("#voice-dialog").close();
      event.target.reset();
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
  const postId = new URL(location.href).searchParams.get("post");
  if (postId) {
    try {
      await openPost(postId);
      return;
    } catch (error) {
      toast(error.message, "error");
    }
  }
  setView("posts");
}

async function bootstrapData() {
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
