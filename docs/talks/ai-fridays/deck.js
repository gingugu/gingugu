// Slides live one per file in slides/, so each stays small enough to edit.
// They are fetched in order and dropped into <main>, then navigation starts.
const SLIDES = [
  "01-title", "02-path", "03-claude-md", "04-memory",
  "06-hooks", "07-permissions", "08-tiers", "09-model-ladder",
  "10-agent-file", "11-delegation", "12-warm-minions", "13-monday",
];

async function load() {
  const deck = document.querySelector(".deck");
  const parts = await Promise.all(
    SLIDES.map((name) => fetch(`slides/${name}.html`).then((r) => r.text()))
  );
  deck.innerHTML = parts.join("\n");
  start([...deck.querySelectorAll(".slide")]);
}

function start(slides) {
  const counter = document.getElementById("counter");
  const progress = document.querySelector(".progress");
  let i = 0;

  const s = parseInt(new URLSearchParams(location.search).get("s"), 10);
  if (s >= 1 && s <= slides.length) i = s - 1;

  function show(n) {
    i = Math.max(0, Math.min(slides.length - 1, n));
    slides.forEach((el, k) => el.classList.toggle("active", k === i));
    counter.textContent = `${i + 1} / ${slides.length}`;
    progress.style.width = `${((i + 1) / slides.length) * 100}%`;
    const url = new URL(location.href);
    url.searchParams.set("s", i + 1);
    history.replaceState(null, "", url);
  }

  document.addEventListener("keydown", (e) => {
    if (["ArrowRight", "PageDown", " "].includes(e.key)) { e.preventDefault(); show(i + 1); }
    if (["ArrowLeft", "PageUp"].includes(e.key)) { e.preventDefault(); show(i - 1); }
    if (e.key === "Home") show(0);
    if (e.key === "End") show(slides.length - 1);
  });
  document.getElementById("prev").addEventListener("click", () => show(i - 1));
  document.getElementById("next").addEventListener("click", () => show(i + 1));

  let x0 = null;
  document.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; }, { passive: true });
  document.addEventListener("touchend", (e) => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0;
    if (Math.abs(dx) > 50) show(i + (dx < 0 ? 1 : -1));
    x0 = null;
  });

  show(i);
}

load().catch((err) => {
  document.querySelector(".deck").innerHTML =
    `<section class="slide active"><h2>Slides failed to load</h2><p class="muted">${err}</p>` +
    `<p class="muted">Serve this folder over HTTP, for example <code>python3 -m http.server</code>.</p></section>`;
});
