// El hormiguero · dirección A «Camino de hormigas».
// Datos: GET /api/jobs cada 5 s (en pausa con la pestaña oculta). Todo dato de un job entra al
// DOM como texto (textContent / createTextNode); nunca se interpreta como HTML.
"use strict";

(function () {
  const POLL_MS = 5000;
  const SVG_NS = "http://www.w3.org/2000/svg";
  // Tablas consultadas con valores de un job: sin prototipo y solo propiedades propias, para que
  // "constructor" o "__proto__" sean datos y no rompan la vista.
  const table = (o) => Object.freeze(Object.assign(Object.create(null), o));
  const lookup = (map, key) => (typeof key === "string" && Object.hasOwn(map, key) ? map[key] : undefined);
  const ICON = table({ run: "◐", ok: "✓", human: "⚑", off: "✕", retry: "↻", wait: "…" });
  const STAGES = [
    { key: "intake", role: "Exploradora", av: "Ex", tech: "intake", ant: true,
      empty: "Sin issues nuevos. La Exploradora espera la etiqueta atta." },
    { key: "spec", role: "Guía · Vigía", av: "Gu", tech: "spec", empty: "Nada que especificar." },
    { key: "build", role: "Cortadora", av: "Co", tech: "build", empty: "La Cortadora no tiene trabajo ahora." },
    { key: "gate", role: "Filtro", av: "Fi", tech: "gate", empty: "Nada por filtrar." },
    { key: "review", role: "Jardineras · Soldado", av: "Ja", tech: "review", empty: "Nada en revisión." },
    { key: "approve", role: "Reina", av: "Re", tech: "approve", empty: "Nada por aprobar." },
    { key: "closed", role: "Cerrados", av: "✓", tech: "merged · rejected", side: true, empty: "Ningún job cerrado todavía." }
  ];
  const STAGE_ROLE = table({
    "intake": "Exploradora", "needs-info": "Exploradora", "spec": "Guía", "spec-review": "Vigía",
    "build": "Cortadora", "gate": "Filtro", "review": "Jardineras", "approve": "Reina",
    "merging": "Merge", "awaiting-merge": "Tú", "needs-human": "Humano", "merged": "Mergeado",
    "rejected": "Rechazado"
  });
  const RISK = table({ low: "bajo", medium: "medio", high: "alto" });

  const $ = (id) => document.getElementById(id);
  const board = $("board");
  const drawer = $("drawer");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  let data = null;
  let lastText = "";
  let lastOk = 0;
  let timer = null;
  let openId = null;
  let opener = null;
  let artsFor = null; // "<id>|<stage_since>|<round>" whose artifacts are on screen

  // --- DOM helpers (sin HTML) ---------------------------------------------------------------

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else el.setAttribute(k, String(v));
    }
    append(el, kids);
    return el;
  }
  function append(el, kids) {
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false || kid === "") continue;
      el.appendChild(typeof kid === "string" || typeof kid === "number"
        ? document.createTextNode(String(kid)) : kid);
    }
  }
  function svg(tag, attrs) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    return el;
  }

  // La hormiga: silueta de brand/assets/Logos/atta-marca.svg (sin el wordmark).
  function ant(label, walking) {
    const s = svg("svg", { viewBox: "0 0 120 100" });
    if (walking) s.setAttribute("class", "walking");
    if (label) { s.setAttribute("role", "img"); s.setAttribute("aria-label", label); }
    else s.setAttribute("aria-hidden", "true");
    const group = (cls, paths, extra) => {
      const g = svg("g", { class: cls });
      for (const d of paths) g.appendChild(svg("path", Object.assign({ d }, extra || {})));
      return g;
    };
    s.appendChild(group("a-leg a-legs-a", ["M56 61 L44 68 L34 85", "M68 60 L81 66 L90 82"]));
    s.appendChild(group("a-leg a-legs-b", ["M62 63 L67 74 L63 89"]));
    s.appendChild(group("a-leg", ["M88 44 L98 35 L107 40"]));
    s.appendChild(svg("path", { class: "a-leaf", d: "M90 56 C100 36 80 12 52 6 C50 26 64 46 90 56 Z" }));
    const vein = svg("g", { class: "a-vein" });
    vein.appendChild(svg("path", { d: "M87 53 C78 38 66 22 55 10", "stroke-width": "2.6" }));
    vein.appendChild(svg("path", { d: "M78 39 L87 33 M78 39 L69 39 M69 27 L77 20 M69 27 L60 27", "stroke-width": "1.8" }));
    s.appendChild(vein);
    const body = svg("g", { class: "a-body" });
    body.appendChild(svg("ellipse", { cx: "27", cy: "62", rx: "19", ry: "15" }));
    body.appendChild(svg("circle", { cx: "49.5", cy: "60", r: "4.5" }));
    body.appendChild(svg("ellipse", { cx: "62", cy: "57", rx: "11", ry: "7", transform: "rotate(-14 62 57)" }));
    body.appendChild(svg("circle", { cx: "83", cy: "51", r: "10" }));
    s.appendChild(body);
    return s;
  }

  // --- formato -------------------------------------------------------------------------------

  function dur(sec) {
    if (typeof sec !== "number" || !isFinite(sec)) return "—";
    sec = Math.round(sec);
    if (sec < 60) return sec + " s";
    const hrs = Math.floor(sec / 3600), min = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (hrs >= 24) { const d = Math.floor(hrs / 24); return d + " d " + (hrs % 24) + " h"; }
    if (hrs) return hrs + " h " + min + " min";
    return min < 10 && s ? min + " min " + s + " s" : min + " min";
  }
  const money = (v) => (typeof v === "number" ? "US$ " + v.toFixed(2) : "US$ —");
  const risk = (r) => lookup(RISK, r) || "sin medir";
  const role = (lens) => (lens === "security" ? "Soldado" : "Jardinera " + lens);

  function stateLabel(j) {
    switch (j.stage) {
      case "intake": return "explorando";
      case "needs-info": return "espera información";
      case "spec": return "escribiendo spec";
      case "spec-review": return "revisando el spec";
      case "build": return j.state === "retry" ? "vuelve a la Cortadora" : "construyendo";
      case "gate": return "pasando el Filtro";
      case "review": return "en revisión";
      case "approve": return "con la Reina";
      case "merging": return "mergeando a " + branch();
      case "awaiting-merge": return "te toca mergear";
      case "needs-human": return "te necesita";
      case "merged": return "mergeado a " + branch();
      case "rejected": return "rechazado";
      default: return j.stage;
    }
  }
  const branch = () => (data && data.integration_branch) || "develop";

  function status(state, label, mini) {
    return h("span", { class: "st st-" + state + (mini ? " st-mini" : "") },
      h("span", { "aria-hidden": "true" }, lookup(ICON, state) || "·"), " " + label);
  }

  // Chips de una ronda: [quién, estado visual, palabra].
  function roundChips(j, r, isCurrent) {
    const out = [];
    if (r.gate) out.push(["Filtro", r.gate.verdict === "PASS" ? "ok" : "retry", r.gate.verdict || "?"]);
    else if (isCurrent && j.stage === "gate") out.push(["Filtro", "run", "corriendo"]);
    for (const rv of r.reviews) out.push([role(rv.lens), rv.verdict === "PASS" ? "ok" : "retry", rv.verdict || "?"]);
    if (isCurrent && j.stage === "review") out.push(["Jardineras", "run", "revisando"]);
    return out;
  }
  function chip(c) {
    const cls = c[1] === "ok" ? "chip ok" : c[1] === "run" ? "chip run" : "chip";
    return h("span", { class: cls }, (lookup(ICON, c[1]) || "·") + " " + c[0] + " · " + c[2]);
  }
  function roundText(j) {
    if (!j.round) return "R —";
    return "R " + j.round + " de " + ((data && data.max_rounds) || 3);
  }

  // --- tablero -------------------------------------------------------------------------------

  function card(j) {
    const last = j.latest;
    const chips = last ? roundChips(j, last, true) : [];
    return h("button", { class: "card", type: "button", "data-id": j.id,
        "aria-expanded": String(j.id === openId), "aria-controls": "drawer" },
      h("span", { class: "row1" },
        h("span", { class: "mono" }, "#" + (j.issue ?? "—") + (j.pr ? " · PR #" + j.pr : "")),
        h("span", { class: "meta" }, "riesgo " + risk(j.risk))),
      h("h3", null, j.title),
      status(j.state, stateLabel(j)),
      chips.length ? h("span", { class: "revs", "aria-label": "Ronda " + last.n }, chips.map(chip)) : null,
      h("span", { class: "foot" },
        h("span", null, roundText(j) + (j.tier ? " · " + j.tier : "")),
        h("span", { class: "mono" }, money(j.cost_usd) + " · " + dur(j.elapsed_s))));
  }

  function column(s, jobs) {
    const busy = jobs.some((j) => j.state === "run");
    const head = h("div", { class: "col-head" },
      h("div", { class: "path-row" },
        h("span", { class: "av" + (busy ? " now" : "") + (s.side ? " out" : ""), "aria-hidden": "true" }, s.av),
        h("span", { class: "dots" })));
    if (busy) {
      const holder = h("span", { class: "col-ant" });
      holder.appendChild(ant(s.role + " trabajando ahora", true));
      head.appendChild(holder);
    }
    head.appendChild(h("h2", { id: "h-" + s.key }, s.role + " ",
      h("span", { class: "count" }, String(jobs.length)),
      busy ? " " : null, busy ? status("run", "en curso", true) : null));
    head.appendChild(h("span", { class: "tech" }, s.tech));
    const body = h("div", { class: "col-body" });
    if (jobs.length) append(body, jobs.map(card));
    else {
      const empty = h("div", { class: "empty" });
      if (s.ant) empty.appendChild(ant("", false));
      empty.appendChild(h("span", null, s.empty));
      body.appendChild(empty);
    }
    return h("section", { class: "col" + (s.side ? " side" : ""), "aria-labelledby": "h-" + s.key }, head, body);
  }

  function renderBoard() {
    const focused = document.activeElement && document.activeElement.closest
      ? document.activeElement.closest(".card") : null;
    const focusId = focused ? focused.dataset.id : null;
    const scroll = board.scrollLeft;
    const cols = STAGES.map((s) => {
      let jobs = data.jobs.filter((j) => j.column === s.key);
      if (s.side) jobs = jobs.slice().sort((a, b) => String(b.ended || "").localeCompare(String(a.ended || "")));
      return column(s, jobs);
    });
    board.replaceChildren(...cols);
    board.scrollLeft = scroll;
    if (focusId) {
      const again = board.querySelector('.card[data-id="' + CSS.escape(focusId) + '"]');
      if (again) again.focus();
    }
  }

  function renderStats() {
    const st = data.stats;
    $("branch").textContent = branch();
    $("s-active").textContent = String(st.active);
    $("s-human").textContent = st.needs_human ? "+ " + st.needs_human + (st.needs_human === 1 ? " te necesita" : " te necesitan") : "";
    $("s-rate").textContent = st.merge_rate === null ? "—" : st.merge_rate + " %";
    $("s-closed").textContent = st.closed ? st.merged + " de " + st.closed + " cerrados" : "sin cerrados";
    $("s-cost").textContent = money(st.cost_usd);
    const list = [["intake", "Exploradora"], ["spec", "Guía"], ["spec-review", "Vigía"], ["build", "Cortadora"],
      ["gate", "Filtro"], ["review", "Jardineras"], ["approve", "Reina"]]
      .filter(([k]) => k in st.p50)
      .map(([k, name]) => h("li", null, h("span", { class: "meta" }, name), h("span", null, dur(st.p50[k]) + blockedNote(st.blocked, k))));
    $("s-p50").replaceChildren(...(list.length ? list : [h("li", null, h("span", { class: "meta" }, "Sin datos todavía"))]));
  }

  // S3: la espera por BLOCKED-BY va aparte; el p50 y el tiempo por etapa ya la excluyen.
  function blockedNote(map, stage) {
    const sec = map ? lookup(map, stage) : undefined;
    return typeof sec === "number" ? " · bloqueado " + dur(sec) : "";
  }

  function renderAlerts() {
    const humans = data.jobs.filter((j) => j.column === "human");
    $("alerts").replaceChildren(...humans.map((j) => h("div", { class: "alert", role: "region", "aria-label": "Necesita humano: #" + (j.issue ?? j.id) },
      status("human", stateLabel(j)),
      h("p", null, h("b", null, "#" + (j.issue ?? "—") + " " + j.title), " — " + humanReason(j)),
      prLink(j),
      h("button", { class: "btn", type: "button", "data-open": j.id }, "Ver #" + (j.issue ?? j.id)))));
  }
  function humanReason(j) {
    if (j.stage === "awaiting-merge") {
      return "La Reina aprobó el PR" + (j.pr ? " #" + j.pr : "") + " (riesgo " + risk(j.risk) +
        "): te toca revisarlo y mergearlo en " + branch() + ".";
    }
    return j.human_reason || "La colonia lo deja en tus manos.";
  }
  // S13: link to the PR a person has to merge. The spine only sends https URLs; checked again.
  function prLink(j) {
    if (j.stage !== "awaiting-merge" || typeof j.pr_url !== "string" || !/^https:\/\/[^\s"'<>]+$/.test(j.pr_url)) return null;
    return h("a", { class: "btn", href: j.pr_url, target: "_blank", rel: "noopener noreferrer" },
      "⚑ Abrir el PR" + (j.pr ? " #" + j.pr : ""));
  }

  function renderWarnings() {
    const el = $("warnings");
    if (data.warnings.length) {
      el.textContent = "Avisos: " + data.warnings.join(" · ");
      el.hidden = false;
    } else el.hidden = true;
  }

  // --- cajón ---------------------------------------------------------------------------------

  function facts(pairs) {
    const dl = h("dl", { class: "facts" });
    for (const [k, v, mono] of pairs) {
      dl.appendChild(h("dt", null, k));
      dl.appendChild(h("dd", mono ? { class: "mono" } : null, v));
    }
    return dl;
  }

  function section(title, ...kids) { return h("div", null, h("h3", null, title), ...kids); }

  function drawerContent(j) {
    const c = j.counters || {};
    const head = h("div", { class: "d-head" },
      h("span", { class: "meta mono" }, "issue #" + (j.issue ?? "—") + (j.pr ? " · PR #" + j.pr : "") + " · rama ",
        h("code", null, j.branch || "—")),
      h("h2", { id: "d-title" }, j.title),
      status(j.state, stateLabel(j)));
    const body = h("div", { class: "d-body" });
    if (j.state === "human") {
      const left = typeof c.max_total_rounds === "number" && typeof c.total_rounds === "number"
        ? Math.max(c.max_total_rounds - c.total_rounds, 0) : null;
      body.appendChild(h("div", { class: "human-box" },
        h("p", null, h("b", null, "⚑ " + humanReason(j))),
        j.stage === "awaiting-merge" ? h("p", null, prLink(j) || "Abre el PR en GitHub, revísalo y mergealo; /atta run cierra el ciclo cuando lo veas mergeado.")
          : j.pr ? h("p", null, "Revisa el PR #" + j.pr + " y decide si vuelve a la Cortadora o se cierra.") : null,
        left === null ? null : h("p", { class: "meta" }, "Tu feedback no reinicia el tope: quedan " + left + " de " + c.max_total_rounds + " rondas totales.")));
    }
    body.appendChild(facts([
      ["Riesgo", risk(j.risk)],
      ["Etapa", j.stage, true],
      ["Ronda", roundText(j).replace(/^R /, "")],
      ["Rondas totales", (c.total_rounds ?? "—") + " de " + (c.max_total_rounds ?? "—")],
      ["Costo", money(j.cost_usd), true],
      ["Tiempo", dur(j.elapsed_s)]
    ]));
    if (j.state === "retry") body.appendChild(h("p", { class: "meta note" }, "↻ La Cortadora retoma en la ronda " + (j.round + 1) + "."));
    const stages = Object.keys(j.durations);
    if (stages.length) {
      body.appendChild(section("Tiempo por etapa",
        facts(stages.map((s) => [(lookup(STAGE_ROLE, s) || s) + " · " + s, dur(j.durations[s]) + blockedNote(j.blocked, s)]))));
    }
    if (j.rounds.length) {
      body.appendChild(section("Revisiones por ronda", h("ol", { class: "rounds" },
        j.rounds.map((r, i) => h("li", null, h("b", null, "Ronda " + r.n),
          roundChips(j, r, i === j.rounds.length - 1).map(chip))))));
    }
    if (j.models.length) {
      body.appendChild(section("Modelo por etapa",
        facts(j.models.map((m) => [lookup(STAGE_ROLE, m.stage) || String(m.stage), (m.tier ? m.tier + " · " : "") + m.model, true]))));
    }
    if (j.warnings.length) body.appendChild(h("p", { class: "meta note" }, "Avisos: " + j.warnings.join(" · ")));
    const arts = h("ul", { class: "arts", id: "arts" });
    body.appendChild(section("Artefactos · atta/jobs/" + j.id + "/", arts));
    return { head, body, arts };
  }

  function loadingRow(text) {
    const loader = h("span", { class: "loader" });
    loader.appendChild(ant("", !reduceMotion.matches));
    return h("li", { class: "art loading", role: "status" }, loader, h("span", { class: "meta" }, text));
  }

  async function loadArtifacts(j, list) {
    list.replaceChildren(loadingRow("La hormiga trae los artefactos…"));
    try {
      const res = await fetch("/api/jobs/" + encodeURIComponent(j.id) + "/files", { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      const payload = await res.json();
      if (openId !== j.id) return;
      const items = payload.files.map((f) => artItem(j, f));
      if (isActive(j)) items.push(loadingRow((lookup(STAGE_ROLE, j.stage) || String(j.stage)) + " está trabajando · " + dur(sinceSec(j))));
      list.replaceChildren(...(items.length ? items : [h("li", { class: "art meta" }, "Todavía no hay artefactos.")]));
    } catch (err) {
      if (openId === j.id) list.replaceChildren(h("li", { class: "art meta" }, "No pude leer los artefactos de este job."));
    }
  }
  const isActive = (j) => j.state === "run" && ["intake", "spec", "spec-review", "build", "gate", "review", "approve", "merging"].includes(j.stage);
  function sinceSec(j) {
    const t = Date.parse(j.stage_since || "");
    const now = Date.parse((data && data.generated) || "") || Date.now();
    return isNaN(t) ? null : Math.max((now - t) / 1000, 0);
  }

  function artItem(j, f) {
    const pre = h("pre", { hidden: true, tabindex: "0" });
    const btn = h("button", { class: "btn btn-sm", type: "button", "aria-expanded": "false" }, "Ver completo");
    btn.addEventListener("click", async () => {
      const show = pre.hidden;
      btn.setAttribute("aria-expanded", String(show));
      btn.textContent = show ? "Ocultar" : "Ver completo";
      pre.hidden = !show;
      if (show && !pre.dataset.loaded) {
        pre.textContent = "Leyendo…";
        try {
          const res = await fetch("/api/jobs/" + encodeURIComponent(j.id) + "/file?path=" + encodeURIComponent(f.path), { cache: "no-store" });
          if (!res.ok) throw new Error(String(res.status));
          pre.textContent = await res.text();
          pre.dataset.loaded = "1";
        } catch (err) {
          pre.textContent = "No pude leer " + f.path + ".";
        }
      }
    });
    return h("li", { class: "art" },
      h("div", { class: "path" }, h("span", null, f.path), btn),
      f.first_line ? h("div", { class: "first" }, f.first_line) : null,
      f.summary ? h("p", { class: "sum" }, f.summary) : null,
      pre);
  }

  function openJob(id, trigger) {
    const j = data && data.jobs.find((x) => x.id === id);
    if (!j) return;
    openId = id;
    artsFor = null;
    if (trigger) opener = trigger;
    renderDrawer(true);
    try { history.replaceState(null, "", "#job=" + encodeURIComponent(id)); } catch (err) { /* sin historial */ }
    if (trigger) $("d-close").focus();
  }

  function renderDrawer(fresh) {
    const j = openId && data.jobs.find((x) => x.id === openId);
    if (!j) { if (!drawer.hidden) closeDrawer(); return; }
    const keepArts = !fresh && artsFor === j.id + "|" + j.stage_since + "|" + j.round ? $("arts") : null;
    const scroll = drawer.querySelector(".d-body") ? drawer.querySelector(".d-body").scrollTop : 0;
    const hadFocus = drawer.contains(document.activeElement) && document.activeElement.id === "d-close";
    const close = h("button", { class: "d-close", id: "d-close", type: "button", "aria-label": "Cerrar detalle" }, "✕");
    close.addEventListener("click", closeDrawer);
    const { head, body, arts } = drawerContent(j);
    if (keepArts) arts.replaceWith(keepArts);
    drawer.replaceChildren(close, head, body);
    if (!fresh) body.scrollTop = scroll;
    drawer.hidden = false;
    document.body.classList.add("drawer-open");
    document.documentElement.style.setProperty("--hdr", document.querySelector("header.top").offsetHeight + "px");
    board.querySelectorAll(".card").forEach((c) => c.setAttribute("aria-expanded", String(c.dataset.id === j.id)));
    if (hadFocus) close.focus();
    if (!keepArts) {
      artsFor = j.id + "|" + j.stage_since + "|" + j.round;
      loadArtifacts(j, arts);
    }
  }

  function closeDrawer() {
    drawer.hidden = true;
    drawer.replaceChildren();
    openId = null;
    artsFor = null;
    document.body.classList.remove("drawer-open");
    board.querySelectorAll(".card").forEach((c) => c.setAttribute("aria-expanded", "false"));
    if (opener && document.contains(opener)) opener.focus();
    else if (opener && opener.dataset && opener.dataset.id) {
      const again = board.querySelector('.card[data-id="' + CSS.escape(opener.dataset.id) + '"]');
      if (again) again.focus();
    }
    opener = null;
    try { history.replaceState(null, "", location.pathname); } catch (err) { /* sin historial */ }
  }

  // --- datos ---------------------------------------------------------------------------------

  function render() {
    renderStats();
    renderAlerts();
    renderBoard();
    renderWarnings();
    if (openId) renderDrawer(false);
  }

  async function poll() {
    try {
      const res = await fetch("/api/jobs", { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      const text = await res.text();
      lastOk = Date.now();
      if (text !== lastText) {
        const first = !data;
        lastText = text;
        data = JSON.parse(text);
        render();
        if (first) {
          $("announce").textContent = data.jobs.length + " jobs en el hormiguero.";
          const m = /^#job=([0-9a-z-]+)$/.exec(location.hash);
          if (m) openJob(m[1], null);
        }
      }
    } catch (err) {
      $("announce").textContent = "No pude leer los jobs. Reintento en 5 s.";
    }
    tick();
  }

  function tick() {
    const live = $("live");
    if (document.hidden) { live.textContent = "En pausa con la pestaña oculta"; return; }
    if (!lastOk) { live.textContent = "Buscando jobs…"; return; }
    const ago = Math.max(Math.round((Date.now() - lastOk) / 1000), 0);
    live.textContent = "Actualizado hace " + ago + " s · cada 5 s";
  }

  function start() {
    stop();
    poll();
    timer = setInterval(poll, POLL_MS);
  }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }

  // --- eventos -------------------------------------------------------------------------------

  board.addEventListener("click", (e) => {
    const c = e.target.closest(".card");
    if (c) openJob(c.dataset.id, c);
  });
  $("alerts").addEventListener("click", (e) => {
    const b = e.target.closest("[data-open]");
    if (b) openJob(b.dataset.open, b);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !drawer.hidden) closeDrawer(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) { stop(); tick(); } else start(); });
  setInterval(tick, 1000);

  const themeBtn = $("theme");
  function setTheme(dark) {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    themeBtn.setAttribute("aria-pressed", String(dark));
  }
  let stored = null;
  try { stored = localStorage.getItem("atta-theme"); } catch (err) { stored = null; }
  setTheme(stored === "dark");
  themeBtn.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme !== "dark";
    setTheme(dark);
    try { localStorage.setItem("atta-theme", dark ? "dark" : "light"); } catch (err) { /* sin almacenamiento */ }
  });

  if (!document.hidden) start(); else tick();
})();
