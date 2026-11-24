// Replays one recorded run (site/data.js, made by experiments/build_site.py). No live server involved.
(() => {
  const D = window.RESILIGUARD;
  if (!D) return;
  const $ = (s) => document.querySelector(s);
  const range = $("#time"), clock = $("#clock"), play = $("#play"), feed = $("#feed"), now = $("#now");
  const SERVERS = ["app1", "app2", "app3"];
  const SPEED = 4; // 1 real second = 4 recorded seconds
  const WORDS = { HEALTHY: "healthy", SUSPECT: "suspect", DOWN: "down", RESTARTING: "restarting", WARMING: "warming up", QUARANTINED: "quarantined" };
  const SOURCE = { chaos: "Fault", controller: "Controller", ai: "AI" };
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  let run, t = 55, playing = false, last = 0; // open just before the fault (60 s)

  const pick = (name) => document.querySelector(`input[name="${name}"]:checked`).value;

  function stateAt(name) {
    // Mode C: the controller's own view. Mode A has no controller: the fault itself decides.
    const hasController = run.events.some((e) => e.src === "state");
    let s = "HEALTHY";
    for (const e of run.events) {
      if (e.t > t) break;
      if (e.server !== name) continue;
      if (hasController && e.src === "state") s = e.state;
      if (!hasController && e.src === "chaos") s = /crashes/.test(e.text) ? "DOWN" : /starts .* again/.test(e.text) ? "HEALTHY" : s;
    }
    return s;
  }

  function render() {
    range.value = t;
    clock.textContent = `${Math.round(t)} s`;
    let sample = null;
    for (const s of run.samples) { if (s[0] <= t) sample = s; else break; }
    const vals = sample ? sample[1] : {};
    const total = SERVERS.reduce((a, n) => a + (vals[n] ? vals[n][0] : 0), 0);
    let bad = 0;
    for (const n of SERVERS) {
      const el = document.getElementById(`s-${n}`), v = vals[n];
      const share = total && v ? v[0] / total : 0;
      el.querySelector(".rps").textContent = `${Math.round(share * 100)}%`;
      el.querySelector(".share i").style.setProperty("--w", `${(share * 100).toFixed(1)}%`);
      el.querySelector(".err").textContent = v ? `${Math.round(v[1] * 100)}%` : "none";
      el.querySelector(".p95").textContent = v ? `${v[2]} ms` : "-";
      if (v && v[1] >= 0.05) el.dataset.sick = ""; else delete el.dataset.sick;
      const st = stateAt(n), tag = el.querySelector(".state");
      tag.dataset.s = st;
      tag.textContent = WORDS[st] || st.toLowerCase();
      bad += v ? v[0] * v[1] : 0;
    }
    // users' view from Locust; the servers' own numbers miss requests sent to a dead server
    let u = null;
    for (const x of run.users || []) { if (x[0] <= t) u = x[1]; else break; }
    now.textContent = `${(((u ?? (total ? bad / total : 0))) * 100).toFixed(1)}%`;
    const items = run.events.filter((e) => e.src !== "state" && e.t <= t).slice(-6).reverse();
    feed.innerHTML = items.length
      ? items.map((e) => `<li data-src="${e.src}"><b>${Math.round(e.t)} s</b><span><span class="src">${SOURCE[e.src]}:</span> ${esc(e.text)}</span></li>`).join("")
      : `<li><b>${Math.round(t)} s</b><span>Nothing yet. The fault starts at 60 s.</span></li>`;
  }

  function setPlaying(on) {
    playing = on;
    play.textContent = on ? "Pause" : t >= run.length ? "Replay" : "Play";
    play.setAttribute("aria-pressed", String(on));
    feed.setAttribute("aria-live", on ? "off" : "polite"); // don't read every step aloud while playing
    if (on) { last = performance.now(); requestAnimationFrame(tick); }
  }

  function tick(ts) {
    if (!playing) return;
    t = Math.min(run.length, t + ((ts - last) / 1000) * SPEED);
    last = ts;
    render();
    if (t >= run.length) setPlaying(false); else requestAnimationFrame(tick);
  }

  function load() {
    run = D.runs[`${pick("mode")}-${pick("scenario")}`];
    range.max = run.length;
    t = Math.min(t, run.length);
    render();
  }

  // a run that wasn't recorded can't be chosen
  for (const input of document.querySelectorAll('#scenario input, #mode input')) {
    input.addEventListener("change", load);
  }
  for (const input of document.querySelectorAll("#mode input")) {
    input.disabled = !Object.keys(D.runs).some((k) => k.startsWith(`${input.value}-`));
  }
  range.addEventListener("input", () => { t = Number(range.value); if (playing) setPlaying(false); render(); });
  play.addEventListener("click", () => {
    if (!playing && t >= run.length) t = 0;
    setPlaying(!playing);
  });
  load();
})();
