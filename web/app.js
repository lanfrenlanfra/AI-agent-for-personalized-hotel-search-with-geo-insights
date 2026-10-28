"use strict";
/* Агент Ква — одностраничный интерфейс без сборки. Маршрутизация по hash. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const main = $("#main");
let META = null;
const state = { compare: new Set(), token: null, map: null };

async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) { o.body = JSON.stringify(opts.body); o.headers["Content-Type"] = "application/json"; if (!opts.method) o.method = "POST"; }
  if (state.token) o.headers["X-Token"] = state.token;
  const r = await fetch(path, o);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("on"), 3600); }

const STATUS = { confirmed: "Подтверждено", likely: "Вероятно", weak: "Слабые данные", disputed: "Спорно", stale: "Устарело", unknown: "Нет данных" };
const VERDICT = { ok: "✓", partial: "◐", no: "✕", maybe: "≈", disputed: "⚡", unknown: "?", stale: "⌛" };
function cert(st) { return `<span class="cert cert-${st}" title="${STATUS[st] || st}"></span>`; }
function stBadge(st) { return `<span class="st st-${st}">${STATUS[st] || st}</span>`; }
function ageText(d) {
  if (d == null) return "";
  if (d <= 0) return "проверено сегодня";
  if (d < 31) return `проверено ${d} дн. назад`;
  if (d < 365) return `проверено ${Math.round(d / 30)} мес. назад`;
  return `проверено ${(d / 365).toFixed(1).replace(".", ",")} г. назад`;
}
function daysSince(iso) { if (!iso) return null; return Math.max(0, Math.round((Date.now() - new Date(iso.slice(0, 10)).getTime()) / 864e5)); }
function rate(v) { return v == null ? "" : Number(v).toFixed(1).replace(".", ","); }
function money(v) { return v ? Math.round(v).toLocaleString("ru-RU") + " ₽" : ""; }
function stars(n) { return n ? "★".repeat(n) : ""; }
function photoBox(h) { return h.photo ? `style="background-image:url('${esc(h.photo)}'),linear-gradient(135deg,#bcd4de,#e9d8bd)"` : ""; }
function initials(n) { return esc((n || "").replace(/[^A-Za-zА-Яа-я ]/g, "").split(" ").filter(Boolean).slice(-2).map(w => w[0]).join("")); }

/* ───────── маршрутизатор ───────── */
const routes = [
  [/^#\/hotel\/(.+)$/, (m) => pageHotel(decodeURIComponent(m[1]))],
  [/^#\/compare\??(.*)$/, (m) => pageCompare(new URLSearchParams(m[1]).get("ids") || "")],
  [/^#\/hotelier$/, () => pageHotelier()],
  [/^#\/quality$/, () => pageQuality()],
  [/^#\/features$/, () => pageFeatures()],
  [/^#\/?(?:\?(.*))?$/, (m) => pageSearch(new URLSearchParams(m[1] || "").get("q") || "")],
];
async function route() {
  const h = location.hash || "#/";
  closeDrawer();
  const nav = h.startsWith("#/hotelier") ? "hotelier" : h.startsWith("#/quality") ? "quality" : h.startsWith("#/features") ? "features" : "search";
  $$("[data-nav]").forEach(a => a.classList.toggle("on", a.dataset.nav === nav));
  main.className = "";
  for (const [rx, fn] of routes) { const m = h.match(rx); if (m) { main.innerHTML = '<div class="loading">Загружаем…</div>'; try { await fn(m); } catch (e) { main.innerHTML = `<div class="empty">Не получилось загрузить: ${esc(e.message)}</div>`; } window.scrollTo(0, 0); return; } }
  main.innerHTML = '<div class="empty">Такой страницы нет. <a href="#/">К поиску</a></div>';
}
window.addEventListener("hashchange", route);

/* ───────── поиск ───────── */
const EXAMPLES = ["тихий номер с видом на закат над Байкалом зимой", "у берега, с баней, до 7000 ₽", "Листвянка, пешком до Шаман-камня, тепло в номере", "Ольхон, вид на скалу Шаманка, с собакой"];
let lastSearch = null;

function intentLabel(it) {
  let s = it.title;
  if (it.kind === "number" && it.threshold) s += ` ≤ ${Math.round(it.threshold)} мин`;
  if (it.scope === "room:yard") s += ", окна во двор";
  if (it.months && it.months.length) s += `, ${it.months.length > 2 ? "сезон" : it.months.map(m => ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"][m - 1]).join(", ")}`;
  if (it.floor) s += `, с ${it.floor}-го этажа`;
  if (it.kind === "bool" && it.want === false) s = "Без: " + s.toLowerCase();
  return s;
}
function filterChips(f) {
  const out = [];
  if (f.price_max) out.push(["price_max", `до ${money(f.price_max)}`]);
  if (f.stars_min) out.push(["stars_min", `от ${f.stars_min}★`]);
  return out;
}

async function pageSearch(q) {
  main.className = "bleed";
  main.innerHTML = `
  <div class="band${q ? " compact" : ""}"><div class="wrap"><section class="hero">
    <h1>Отель на Байкале, который подойдёт именно вам</h1>
    <p class="lead">Опишите поездку своими словами: закат из окна, тишина, вид на лёд, путь до берега. Мы проверим по карте и отзывам и покажем, откуда взяли каждый факт.</p>
    <form class="ask" id="askForm"><label class="sr" for="q" style="position:absolute;left:-999px">Что важно в отеле</label>
      <textarea id="q" rows="2" placeholder="Например: тихий номер с видом на закат, рядом с берегом, зимой">${esc(q)}</textarea>
      <button type="submit">Найти отель</button></form>
    <div class="examples">${EXAMPLES.map(e => `<button type="button" class="ex">${esc(e)}</button>`).join("")}</div>
    <div class="crit" id="crit" aria-live="polite"></div>
  </section></div></div>
  <div class="wrap">
  <div class="legend" aria-label="Обозначения"><span>${cert("confirmed")}подтверждено</span><span>${cert("likely")}вероятно</span><span>${cert("weak")}слабые данные</span><span>${cert("disputed")}источники спорят</span><span>${cert("stale")}устарело</span><span>${cert("unknown")}данных нет</span></div>
  <div class="split"><div class="results" id="results"></div><div class="stick"><div id="map"></div></div></div></div>`;
  $("#askForm").onsubmit = e => { e.preventDefault(); const v = $("#q").value.trim(); location.hash = "#/?q=" + encodeURIComponent(v); };
  $("#q").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#askForm").requestSubmit(); } });
  $$(".ex").forEach(b => b.onclick = () => { location.hash = "#/?q=" + encodeURIComponent(b.textContent); });
  initMap();
  await runSearch(q, null);
}

let pollTimer = null;
async function runSearch(q, override, passive) {
  const box = $("#results");
  clearTimeout(pollTimer);
  if (!passive) box.innerHTML = '<div class="loading">Агент Ква сверяет карту и отзывы…</div>';
  const res = await api("/api/search", { body: { q, ...(override || {}), ...(passive ? { passive: true } : {}) } });
  if (!$("#results")) return;
  lastSearch = { q, res, override };
  renderCrit(res.parsed, q);
  const cov = res.coverage || {};
  // пока фоновая проверка идёт, тихо обновляем выдачу: новые отели появляются сами
  if (cov.pending > 0 && cov.enabled) pollTimer = setTimeout(() => { if (location.hash.startsWith("#/") && !location.hash.startsWith("#/hotel")) runSearch(q, override, true).catch(() => {}); }, 6000);
  if (!res.results.length && !(cov.candidates || []).length) { box.innerHTML = '<div class="empty">Ничего не нашлось. Попробуйте описать иначе или уберите часть условий.</div>'; return; }
  box.innerHTML = coverageHtml(cov, res.results.length) + res.results.map(cardHtml).join("") + candidatesHtml(cov);
  $$(".cmp input", box).forEach(c => { c.checked = state.compare.has(c.value); c.onchange = () => { c.checked ? state.compare.add(c.value) : state.compare.delete(c.value); cmpBar(); }; });
  cmpBar();
  drawMarkers(res.results);
}
function renderCrit(parsed, q) {
  const c = $("#crit");
  if (!parsed.intents.length && !Object.keys(parsed.filters).length) { c.innerHTML = q ? '<span class="muted">Не нашли в запросе известных характеристик — показываем всё. Попробуйте: «тихий», «закат», «до метро», «с собакой».</span>' : '<span class="muted">Показаны все отели. Опишите, что для вас важно.</span>'; return; }
  c.innerHTML = '<span class="muted">Мы поняли так:</span>' + parsed.intents.map((it, i) => `<span class="chip">${esc(intentLabel(it))}<button data-i="${i}" aria-label="Убрать критерий">×</button></span>`).join("")
    + filterChips(parsed.filters).map(([k, t]) => `<span class="chip flt">${esc(t)}<button data-f="${k}" aria-label="Убрать фильтр">×</button></span>`).join("")
    + (parsed.parsed_by === "llm" ? '<span class="pill">разобрано LLM</span>' : "");
  $$("button[data-i]", c).forEach(b => b.onclick = () => { const ints = parsed.intents.filter((_, i) => i !== +b.dataset.i); runSearch(q, { intents: ints, filters: parsed.filters }); });
  $$("button[data-f]", c).forEach(b => b.onclick = () => { const f = { ...parsed.filters }; delete f[b.dataset.f]; runSearch(q, { intents: parsed.intents, filters: f }); });
}
function coverageHtml(cov, shown) {
  if (!cov || !cov.total) return "";
  const rest = cov.enriched < cov.total;
  const live = cov.pending > 0
    ? `<span class="pulse" aria-hidden="true"></span> Изучаем ещё ${cov.pending}, список обновится сам.`
    : !rest ? "" : cov.enabled ? "Подходящие под ваш запрос мы изучаем автоматически." : "Автоматическая проверка выключена.";
  return `<div class="cover" role="status"><b>Подробно изучено ${cov.enriched} из ${cov.total} отелей региона.</b>${rest ? " По остальным пока есть только данные каталога: цена, оценка, расстояния." : ""} ${live}</div>`;
}
function candidatesHtml(cov) {
  const cs = (cov && cov.candidates) || [];
  if (!cs.length) return "";
  const st = { candidate: "в очереди", queued: "в очереди", running: "изучаем сейчас…", failed: "не удалось загрузить" };
  return `<h3 class="cand-h">Возможно подойдут: изучаем</h3>` + cs.map(c => `<article class="card cand">
    <div class="bd"><h3>${esc(c.name)}</h3>
    <div class="meta"><span>${stars(c.stars)}</span>${c.rating ? `<span>оценка ${rate(c.rating)} (${c.reviews} отз.)</span>` : ""}${c.price ? `<span>от ${money(c.price)}</span>` : ""}${c.subway ? `<span>метро ${esc(c.subway)}</span>` : ""}<span>${esc(c.address || "")}</span></div>
    <ul class="why">${(c.notes || []).map(n => `<li><span class="vd vd-maybe">≈</span><span>${esc(n)} <span class="age">данные каталога, не проверено</span></span></li>`).join("")}</ul>
    <div class="muted"><span class="pill">${st[c.status] || st.candidate}</span>${c.status === "failed" && c.error ? ` ${esc(c.error)}` : ""}</div></div></article>`).join("");
}
function cardHtml(r) {
  const lb = r.label === "Не подходит" ? "lb-Не" : r.label.startsWith("Подходит") ? "lb-Подходит" : "";
  const items = r.items.filter(i => i.feature !== "text").slice(0, 5);
  return `<article class="card" data-id="${esc(r.id)}">
    <a class="ph" href="#/hotel/${encodeURIComponent(r.id)}" aria-label="${esc(r.name)}" ${photoBox(r)}>${r.photo ? "" : initials(r.name)}</a>
    <div class="bd">
      <div class="top-row"><h3><a href="#/hotel/${encodeURIComponent(r.id)}">${esc(r.name)}</a></h3>${r.price_from ? `<div class="price">от ${money(r.price_from)}<small>за ночь</small></div>` : ""}</div>
      <div class="meta">${r.stars ? `<span class="st-stars" title="${r.stars} зв.">${stars(r.stars)}</span>` : ""}${r.rating ? `<span>оценка ${rate(r.rating)}${r.reviews ? ` · ${r.reviews} отз.` : ""}</span>` : ""}<span>${esc(shortAddr(r.address))}</span></div>
      ${items.length ? `<div class="score"><div class="bar" role="img" aria-label="Совпадение ${r.score}%"><i style="width:${r.score}%"></i></div><span class="num">${r.score}%</span><span class="label ${lb}">${esc(r.label)}</span></div>` : ""}
      <ul class="why">${items.map(i => `<li><span class="vd vd-${i.verdict}" title="${i.verdict}">${VERDICT[i.verdict] || "?"}</span><span><b>${esc(i.title)}</b>${i.scope_title && i.scope !== "hotel" ? ` <span class="pill">${esc(i.scope_title)}</span>` : ""} — ${esc(i.conflict && i.verdict === "disputed" ? i.conflict : i.text)} ${i.age_days != null ? `<span class="age">${ageText(i.age_days)}</span>` : ""}</span></li>`).join("")}</ul>
      <label class="cmp"><input type="checkbox" value="${esc(r.id)}"> Добавить в сравнение</label>
    </div></article>`;
}
function shortAddr(a) { return String(a || "").replace(/^Россия,?\s*/, "").replace(/Иркутская область,?\s*/, "").replace(/Иркутский район,?\s*/, "").replace(/Ольхонский район,?\s*/, ""); }
function cmpBar() {
  let b = $("#cmpbar");
  if (state.compare.size < 1) { b && b.remove(); return; }
  if (!b) { b = document.createElement("div"); b.id = "cmpbar"; b.className = "cmpbar"; document.body.appendChild(b); }
  b.innerHTML = `<span>Выбрано: ${state.compare.size}</span><button ${state.compare.size < 2 ? "disabled" : ""} id="goCmp">Сравнить</button><button class="quiet" id="clrCmp">Сбросить</button>`;
  $("#goCmp").onclick = () => { location.hash = "#/compare?ids=" + [...state.compare].map(encodeURIComponent).join(","); };
  $("#clrCmp").onclick = () => { state.compare.clear(); $$(".cmp input").forEach(c => c.checked = false); cmpBar(); };
}
let markers = null;
function initMap() {
  if (state.map) { state.map.remove(); state.map = null; }
  if (!window.L) return;
  state.map = L.map("map", { scrollWheelZoom: false }).setView([52.4, 106.3], 7);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(state.map);
  markers = L.layerGroup().addTo(state.map);
}
function drawMarkers(rs) {
  if (!state.map || !markers) return;
  markers.clearLayers();
  const pts = [];
  rs.forEach(r => {
    if (r.lat == null) return;
    const col = r.score >= 70 ? "#0e8f7e" : r.score >= 45 ? "#f2a33a" : "#8a7a5c";
    const m = L.circleMarker([r.lat, r.lon], { radius: 9, color: "#14232b", weight: 2, fillColor: col, fillOpacity: .95 }).addTo(markers);
    m.bindTooltip(`${r.name} — ${r.score}%`);
    m.on("click", () => { const el = $(`.card[data-id="${CSS.escape(r.id)}"]`); if (el) el.scrollIntoView({ behavior: "smooth", block: "center" }); });
    pts.push([r.lat, r.lon]);
  });
  if (pts.length) state.map.fitBounds(pts, { padding: [30, 30], maxZoom: 15 });
}

/* ───────── панель «Откуда это» ───────── */
function closeDrawer() { const d = $("#drawer"); d.classList.remove("open"); d.setAttribute("aria-hidden", "true"); }
async function openSource(hid, feature, scope) {
  const d = $("#drawer");
  d.innerHTML = '<div class="loading">Загружаем…</div>'; d.classList.add("open"); d.setAttribute("aria-hidden", "false");
  const r = await api(`/api/hotels/${encodeURIComponent(hid)}/facts/${feature}?scope=${encodeURIComponent(scope)}`);
  const f = r.fact;
  d.innerHTML = `<button class="quiet x" id="dx" aria-label="Закрыть">Закрыть</button>
    <h2 style="max-width:20ch">${esc(f.title)}</h2>
    <p>${cert(f.status)} ${stBadge(f.status)} <span class="pill">уверенность ${Math.round(f.confidence * 100)}%</span> <span class="pill">${esc(f.scope_title)}</span></p>
    <p><b>${esc(f.summary || "")}</b></p>
    <p class="muted">${esc(r.spec.description || "")}</p>
    <p class="muted">Свежесть: значение актуально до ${esc(f.expires_at || "—")} (срок жизни этой характеристики — ${r.spec.ttl_days} дн.).</p>
    ${f.conflict ? `<div class="warn"><b>${f.conflict.level === "hard" ? "Источники спорят" : "Есть расхождение"}.</b> ${esc(f.conflict.text)}</div>` : ""}
    ${f.conflict ? `<p style="margin-top:12px">${esc(f.explanation.split(". ").slice(-1)[0])}</p>` : `<p style="margin-top:12px">${esc(f.explanation)}</p>`}
    <h3 style="margin-top:18px">Свидетельства (${r.observations.length})</h3>
    ${r.observations.map(o => `<div class="ob ${o.superseded ? "old" : ""}">
      <div><b>${esc(o.source_title)}</b> ${o.simulated ? '<span class="pill">имитация</span>' : ""} ${o.superseded ? '<span class="pill">заменено новым</span>' : ""}</div>
      <div>${esc(o.summary || "")}</div>
      ${(o.evidence || []).map(e => `<q>${esc(e.quote)}${e.date ? ` <small>(${esc(e.date)}${e.polarity ? ", " + (e.polarity === "за" ? "подтверждает" : "опровергает") : ""})</small>` : ""}</q>`).join("")}
      <small>на дату ${esc(o.observed_at)} · собрано ${esc((o.collected_at || "").slice(0, 10))} · уверенность ${Math.round(o.confidence * 100)}% · вес с учётом свежести ${o.freshness_weight}<br>Метод: ${esc(o.method || "—")}</small></div>`).join("")}
    ${r.scopes.length > 1 ? `<h3>Другие области</h3>${r.scopes.filter(s => s.scope !== scope).map(s => `<p>${cert(s.status)} <b>${esc(s.scope_title)}</b>: ${esc(s.summary)} <button class="link" data-sc="${esc(s.scope)}">подробнее</button></p>`).join("")}` : ""}`;
  $("#dx").onclick = closeDrawer;
  $$("[data-sc]", d).forEach(b => b.onclick = () => openSource(hid, feature, b.dataset.sc));
  $("#dx").focus();
}
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

/* ───────── карточка отеля ───────── */
const SIDES = [["запад", 270], ["северо-запад", 315], ["север", 0], ["северо-восток", 45], ["восток", 90], ["юго-восток", 135], ["юг", 180], ["юго-запад", 225]];
const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];

function factRows(list) {
  const byF = new Map();
  list.forEach(f => { if (!byF.has(f.feature)) byF.set(f.feature, []); byF.get(f.feature).push(f); });
  return [...byF.values()].map(fs => {
    const main_ = fs.find(f => f.scope === "hotel") || fs[0];
    const rest = fs.filter(f => f !== main_).sort((a, b) => a.scope.localeCompare(b.scope));
    const age = daysSince(main_.observed_at);
    return `<div class="frow" data-f="${esc(main_.feature)}">
      ${cert(main_.status)}<div class="t">${esc(main_.title)}${main_.scope !== "hotel" ? ` <span class="pill">${esc(main_.scope_title)}</span>` : ""}</div>
      <div class="s">${esc(main_.summary || "")}${main_.conflict ? `<div class="warn">${esc(main_.conflict.level === "hard" ? "Источники спорят: " : "Есть расхождение: ")}${esc(main_.conflict.text.replace("Источники расходятся: ", ""))}</div>` : ""}</div>
      <div class="rt">${stBadge(main_.status)}<span class="pill num">${Math.round(main_.confidence * 100)}%</span><span class="age">${ageText(age)}</span><button class="link" data-src="${esc(main_.feature)}" data-scope="${esc(main_.scope)}">Откуда это?</button></div>
    </div>
    ${rest.length ? `<details class="frow scoped" style="display:block"><summary>По сторонам и номерам: ${rest.length}</summary>${rest.map(r => `<div style="display:grid;grid-template-columns:20px 200px 1fr auto;gap:8px;padding:5px 0;align-items:baseline">${cert(r.status)}<span>${esc(r.scope_title)}</span><span>${esc(r.summary || "")}</span><button class="link" data-src="${esc(r.feature)}" data-scope="${esc(r.scope)}">Откуда?</button></div>`).join("")}</details>` : ""}`;
  }).join("");
}

async function pageHotel(id) {
  const h = await api("/api/hotels/" + encodeURIComponent(id));
  const gal = h.photos.length ? `<div class="gallery">${h.photos.slice(0, 5).map(u => `<div style="background-image:url('${esc(u)}')" data-big="${esc(u)}"></div>`).join("")}</div>` : '<div class="gallery empty"></div>';
  main.innerHTML = `
  <div class="crumbs"><a href="#/">← К поиску</a></div>
  ${gal}
  <div class="htitle"><div><h1>${esc(h.name)}</h1><div class="meta">${h.stars ? `<span class="st-stars">${stars(h.stars)}</span>` : ""}${h.rating ? `<span>оценка ${rate(h.rating)} (${h.reviews} отз.)</span>` : ""}${h.price_from ? `<span>от ${money(h.price_from)}</span>` : ""}<span>${esc(h.address || "")}</span>${h.stats.floors ? `<span>${h.stats.floors} эт.</span>` : ""}${h.stats.year_built ? `<span>${h.stats.year_built} г.</span>` : ""}</div></div>
    <div>${h.url && !h.synthetic ? `<a class="btn ghost" href="${esc(h.url)}" target="_blank" rel="noopener">Страница на Островке</a>` : ""} <button class="quiet" id="cmpAdd">В сравнение</button></div></div>
  ${h.description ? `<p class="muted" style="max-width:80ch">${esc(h.description.slice(0, 420))}${h.description.length > 420 ? "…" : ""}</p>` : ""}
  <section class="blk"><h2>Что мы знаем об отеле</h2>
    <div class="legend"><span>${cert("confirmed")}подтверждено</span><span>${cert("likely")}вероятно</span><span>${cert("weak")}слабые данные</span><span>${cert("disputed")}источники спорят</span><span>${cert("stale")}устарело</span></div>
    <div class="facts">${h.groups.map(g => `<div class="fgroup"><h3>${esc(g.title)}</h3>${factRows(g.facts)}</div>`).join("") || '<div class="empty">Данных пока нет.</div>'}</div></section>
  <section class="blk"><h2>Вид из окна</h2>
    <p class="muted" style="max-width:75ch">Силуэт застройки на горизонте из окна выбранного этажа и путь солнца в день заката. Если солнце в момент заката выше силуэта — закат виден.</p>
    <div class="ctrl"><div><label for="hzSide">Окна на</label><select id="hzSide">${SIDES.map(s => `<option value="${s[1]}">${s[0]}</option>`).join("")}</select></div>
      <div><label for="hzFloor">Этаж: <b id="hzFloorV">5</b></label><input id="hzFloor" type="range" min="1" max="${Math.max(10, h.stats.floors || 12)}" value="5"></div>
      <div><label for="hzMonth">Месяц</label><select id="hzMonth">${MONTHS.map((m, i) => `<option value="${i + 1}" ${i + 1 === new Date().getMonth() + 1 ? "selected" : ""}>${m}</option>`).join("")}</select></div></div>
    <canvas id="hz" class="hz" width="1000" height="300" role="img" aria-label="Силуэт застройки и путь солнца"></canvas><div class="verdict" id="hzVerdict"></div>
    <div id="hmap" class="map" style="margin-top:14px;height:340px"></div>
    <p class="muted" style="margin-top:6px;font-size:13px">Здания: чем выше, тем темнее. Контуры и высоты — OpenStreetMap; если высоты нет в OSM, берём типовую.</p></section>
  <div class="two">
  <section class="blk" style="margin-top:18px"><h2>Вопросы отельеру</h2>
    ${h.questions.length ? h.questions.map(q => `<div class="q" data-q="${q.id}"><div><b>${esc(q.title)}</b> <span class="pill">${{ unknown: "нет данных", disputed: "источники спорят", conflict: "есть расхождение", stale: "данные устарели", weak: "слабые данные" }[q.reason] || q.reason}</span></div><div>${esc(q.text)}</div>
      <textarea rows="2" placeholder="Ответ отельера своими словами, например: «Закат виден из номеров на запад с 5 этажа»"></textarea>
      <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="quiet qs">Записать ответ (имитация)</button><button class="ghost qsim">Ответить за отельера</button></div><div class="parsed" hidden></div></div>`).join("") : '<div class="empty">Открытых вопросов нет: данные согласованы и свежие.</div>'}
    <p class="muted" style="font-size:13px">Настоящие ответы отельер даёт в <a href="#/hotelier">кабинете</a>. Здесь ответы помечаются как имитация и весят меньше.</p></section>
  <section class="blk" style="margin-top:18px"><h2>Свежесть и перепроверка</h2>
    <p class="muted">Отели редко обновляют данные, поэтому каждое значение имеет срок жизни. Можно перепроверить сейчас или смоделировать, как изменится факт, если рядом построят дом.</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><button id="rf">Перепроверить сейчас</button><button class="quiet" id="bld">Демо: рядом построили дом (40 м, запад)</button>${h.overlay ? '<button class="quiet" id="bldx">Убрать демо-дом</button>' : ""}</div>
    <h3 style="margin-top:16px">История изменений</h3>
    <ul class="events" id="evs">${h.events.length ? h.events.map(e => `<li>${esc(e.text)} <small class="muted">${esc((e.ts || "").slice(0, 16).replace("T", " "))}</small></li>`).join("") : '<li class="muted">Пока без изменений.</li>'}</ul></section></div>
  <section class="blk"><h2>Отзывы гостей</h2>${h.guest_photos.length ? `<div class="photos-row" style="margin-bottom:10px">${h.guest_photos.slice(0, 12).map(u => `<img loading="lazy" src="${esc(u)}" alt="Фото гостей">`).join("")}</div>` : ""}
    ${h.reviews_list.slice(0, 10).map(r => `<div class="rev"><small class="muted">${esc(r.date || "")}${r.rating ? " · " + r.rating + "/10" : ""}${r.room ? " · " + esc(r.room) : ""}</small>${r.plus ? `<div><b class="p">+</b> ${esc(r.plus)}</div>` : ""}${r.minus ? `<div><b class="m">−</b> ${esc(r.minus)}</div>` : ""}${r.text ? `<div>${esc(r.text)}</div>` : ""}</div>`).join("") || '<div class="empty">Отзывов нет.</div>'}${h.reviews_list.length > 10 ? `<p class="muted" style="margin-top:12px">Показаны 10 из ${h.reviews_list.length} отзывов. Остальные на <a href="${esc(h.url || "#")}" target="_blank" rel="noopener">странице отеля на Островке</a>.</p>` : ""}</section>`;
  $$("[data-big]").forEach(d => d.onclick = () => window.open(d.dataset.big, "_blank"));
  $$("[data-src]").forEach(b => b.onclick = () => openSource(id, b.dataset.src, b.dataset.scope));
  $("#cmpAdd").onclick = () => { state.compare.add(id); cmpBar(); toast("Добавлено в сравнение"); };
  // вопросы
  $$(".q").forEach(el => {
    const qid = el.dataset.q, out = $(".parsed", el);
    const show = r => { out.hidden = false; out.innerHTML = `<b>Разобрано (${r.parsed_by === "llm" ? "LLM" : "правила"}):</b> ` + (r.fields.length ? r.fields.map(f => `${esc(f.scope_title)}: ${f.value === true ? "да" : f.value === false ? "нет" : esc(f.value)}${f.floor_from ? `, с ${f.floor_from}-го этажа` : ""}`).join("; ") : "не удалось выделить значения — переформулируйте"); };
    $(".qs", el).onclick = async () => { const t = $("textarea", el).value.trim(); if (!t) return toast("Напишите ответ"); try { show(await api(`/api/questions/${qid}/answer`, { body: { text: t, simulated: true } })); setTimeout(() => pageHotel(id), 1800); } catch (e) { toast(e.message); } };
    $(".qsim", el).onclick = async () => { try { const r = await api(`/api/questions/${qid}/simulate`, { method: "POST", body: {} }); $("textarea", el).value = r.answer_text; show(r); setTimeout(() => pageHotel(id), 2200); } catch (e) { toast(e.message); } };
  });
  const act = async (fn, msg) => { try { const r = await fn(); toast(`${msg}: изменилось значений — ${r.changes.length}`); await pageHotel(id); } catch (e) { toast(e.message); } };
  $("#rf").onclick = () => act(() => api(`/api/hotels/${encodeURIComponent(id)}/refresh`, { body: {} }), "Перепроверено");
  $("#bld").onclick = () => act(() => api(`/api/hotels/${encodeURIComponent(id)}/demo/building`, { body: { bearing: 270, dist: 40, height: 60 } }), "Дом построен");
  if ($("#bldx")) $("#bldx").onclick = () => act(() => api(`/api/hotels/${encodeURIComponent(id)}/demo/building`, { method: "DELETE" }), "Дом убран");
  // вид из окна
  let tmr;
  const upd = () => { clearTimeout(tmr); tmr = setTimeout(() => drawHorizon(id), 120); };
  $("#hzFloor").oninput = () => { $("#hzFloorV").textContent = $("#hzFloor").value; upd(); };
  $("#hzSide").onchange = upd; $("#hzMonth").onchange = upd;
  drawHorizon(id); drawBuildingsMap(h);
}

async function drawHorizon(id) {
  const az = $("#hzSide").value, floor = $("#hzFloor").value, month = $("#hzMonth").value;
  const d = await api(`/api/hotels/${encodeURIComponent(id)}/horizon?az=${az}&floor=${floor}&month=${month}`);
  const cv = $("#hz"); if (!cv) return;
  const W = cv.width = cv.clientWidth * (window.devicePixelRatio || 1), H = cv.height = 300 * (window.devicePixelRatio || 1);
  const g = cv.getContext("2d");
  if (!d.available) { g.fillStyle = "#1b2a3a"; g.fillRect(0, 0, W, H); g.fillStyle = "#fff"; g.font = `${14 * (devicePixelRatio || 1)}px "Golos Text", system-ui, sans-serif`; g.fillText("Нет данных карты для этого отеля — соберите тайлы OSM (collect_osm.py)", 20, 40); $("#hzVerdict").textContent = ""; return; }
  const span = 62, minEl = -3, maxEl = 32, k = devicePixelRatio || 1;
  const X = rel => (rel + span) / (2 * span) * W, Y = el => H - (el - minEl) / (maxEl - minEl) * H;
  const sky = g.createLinearGradient(0, 0, 0, H); sky.addColorStop(0, "#1c2757"); sky.addColorStop(.6, "#6d4a7e"); sky.addColorStop(1, "#f2a33a");
  g.fillStyle = sky; g.fillRect(0, 0, W, H);
  // поле зрения окна
  g.fillStyle = "#ffffff12"; g.fillRect(X(-d.fov), 0, X(d.fov) - X(-d.fov), H);
  g.strokeStyle = "#ffffff55"; g.setLineDash([4 * k, 5 * k]); [-d.fov, d.fov].forEach(r => { g.beginPath(); g.moveTo(X(r), 0); g.lineTo(X(r), H); g.stroke(); }); g.setLineDash([]);
  // путь солнца
  const path = d.sun_path.filter(p => Math.abs(p.rel) <= span);
  g.strokeStyle = "#ffe3a8"; g.lineWidth = 2 * k; g.setLineDash([2 * k, 6 * k]); g.beginPath(); path.forEach((p, i) => i ? g.lineTo(X(p.rel), Y(p.alt)) : g.moveTo(X(p.rel), Y(p.alt))); g.stroke(); g.setLineDash([]);
  // силуэт
  g.fillStyle = "#0e1622"; g.beginPath(); g.moveTo(0, H);
  d.profile.forEach(p => g.lineTo(X(p.rel), Y(Math.max(p.el, minEl)))); g.lineTo(W, H); g.closePath(); g.fill();
  // солнце
  if (d.sunset_rel != null && Math.abs(d.sunset_rel) <= span) {
    const sx = X(d.sunset_rel), sy = Y(2);
    g.fillStyle = d.sunset_visible ? "#ffd166" : "#ffd16688"; g.beginPath(); g.arc(sx, sy, 11 * k, 0, 7); g.fill();
    g.strokeStyle = d.sunset_visible ? "#0e8f7e" : "#c2255c"; g.lineWidth = 3 * k; g.beginPath(); g.arc(sx, sy, 15 * k, 0, 7); g.stroke();
  }
  g.fillStyle = "#fff"; g.font = `${12 * k}px "Golos Text", system-ui, sans-serif`;
  [-45, 0, 45].forEach(r => { const a = ((d.facade_az + r) % 360 + 360) % 360; g.fillText(Math.round(a) + "°", X(r) - 10 * k, H - 6 * k); });
  const v = d.sunset_visible;
  $("#hzVerdict").innerHTML = v === null ? `Окна на ${esc(d.side)}: в ${MONTHS[month - 1].replace(/ь$/, "е").replace(/т$/, "те")} солнце садится вне поля зрения этих окон.` :
    v ? `<span class="vd-ok">✓</span> С ${floor}-го этажа окон на ${esc(d.side)} закат виден (${MONTHS[month - 1]}).` : `<span class="vd-no">✕</span> С ${floor}-го этажа окон на ${esc(d.side)} закат закрыт застройкой (${MONTHS[month - 1]}). Попробуйте этаж выше.`;
  $("#hzVerdict").innerHTML += ` <small class="muted">Карта OSM от ${esc(d.osm_date || "—")}${d.real_footprint ? "" : "; контур здания не найден, окна смоделированы"}</small>`;
}
async function drawBuildingsMap(h) {
  if (!window.L || !$("#hmap")) return;
  const m = L.map("hmap", { scrollWheelZoom: false }).setView([h.lat, h.lon], 18);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(m);
  L.marker([h.lat, h.lon]).addTo(m).bindTooltip(h.name);
  try {
    const b = await api(`/api/hotels/${encodeURIComponent(h.id)}/map`);
    (b.buildings || []).forEach(x => L.polygon(x.p, { color: x.overlay ? "#c2255c" : x.own ? "#3b3fd8" : "#14232b", weight: x.own || x.overlay ? 3 : 1, fillColor: x.overlay ? "#c2255c" : "#14232b", fillOpacity: Math.min(.7, .1 + x.h / 80) }).addTo(m).bindTooltip(`${x.h} м${x.explicit ? "" : " (оценка)"}${x.overlay ? " — демо-здание" : ""}`));
  } catch { /* карта зданий необязательна */ }
}

/* ───────── сравнение ───────── */
async function pageCompare(ids) {
  const list = ids.split(",").filter(Boolean).map(decodeURIComponent);
  if (list.length < 2) { main.innerHTML = '<div class="crumbs"><a href="#/">← К поиску</a></div><div class="empty">Выберите в поиске минимум два отеля и нажмите «Сравнить».</div>'; return; }
  const r = await api("/api/compare?ids=" + list.map(encodeURIComponent).join(","));
  const cell = (c, best) => !c ? '<span class="muted">нет данных</span>' : `${cert(c.status)} ${esc(c.summary || String(c.value))} <span class="age">${Math.round(c.confidence * 100)}%${c.age_days != null ? " · " + ageText(c.age_days) : ""}</span>`;
  main.innerHTML = `<div class="crumbs"><a href="#/">← К поиску</a></div><h2>Сравнение</h2>
  <p class="muted">Сверху — характеристики, по которым отели различаются. Зелёным выделено лучшее значение.</p>
  <section class="blk" style="padding:0;overflow:auto"><table><thead><tr><th>Характеристика</th>${r.names.map((n, i) => `<th><a href="#/hotel/${encodeURIComponent(r.hotels[i])}">${esc(n)}</a></th>`).join("")}</tr></thead><tbody>
  ${r.rows.map(row => `<tr><td><b>${esc(row.title)}</b>${row.differs ? "" : ' <span class="pill">одинаково</span>'}</td>${row.cells.map((c, i) => `<td class="${row.best === i ? "cell-best" : ""}">${cell(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></section>`;
}

/* ───────── кабинет отельера ───────── */
async function pageHotelier() {
  if (!state.token) {
    main.innerHTML = `<section class="hero"><h1>Кабинет отельера</h1><p class="muted" style="max-width:60ch">Отельер отвечает на вопросы системы своими словами, подтверждает или исправляет факты. Его слово весомее всего, но физика (карта) всё равно сверяется.</p>
    <form id="lf" class="blk" style="max-width:420px;margin:0"><label for="lg">Логин</label><input id="lg" value="hotelier" autocomplete="username"><label for="pw">Пароль</label><input id="pw" type="password" autocomplete="current-password"><p class="muted" style="font-size:13px;margin-top:8px">Демо-доступ: hotelier / hotelier123 (задаётся в .env).</p><button type="submit">Войти</button></form></section>`;
    $("#lf").onsubmit = async e => { e.preventDefault(); try { const r = await api("/api/hotelier/login", { body: { login: $("#lg").value, password: $("#pw").value } }); state.token = r.token; sessionStorage.setItem("lupa_token", r.token); pageHotelier(); } catch (er) { toast(er.message); } };
    return;
  }
  const [qs, fr, feats] = await Promise.all([api("/api/questions"), api("/api/freshness"), api("/api/features")]);
  const kinds = Object.fromEntries(feats.map(f => [f.key, f]));
  const todo = [...fr.disputed, ...fr.stale];
  main.innerHTML = `<div class="htitle" style="margin-top:24px"><h1>Кабинет отельера</h1><button class="quiet" id="lo">Выйти</button></div>
  <section class="blk"><h2>Вопросы от системы (${qs.length})</h2>
    ${qs.length ? qs.map(q => `<div class="q" data-q="${q.id}"><div><b>${esc(q.hotel)}</b> — ${esc(q.title)} <span class="pill">${{ unknown: "нет данных", disputed: "источники спорят", conflict: "есть расхождение", stale: "данные устарели", weak: "слабые данные" }[q.reason] || q.reason}</span></div><div>${esc(q.text)}</div>
      <textarea rows="2" placeholder="Ответьте как обычно ответили бы коллеге: «Закат виден из номеров на запад с 5 этажа, во двор тихо»"></textarea><div><button class="send">Отправить ответ</button></div><div class="parsed" hidden></div></div>`).join("") : '<div class="empty">Вопросов нет — всё согласовано.</div>'}</section>
  <section class="blk"><h2>Подтвердить или исправить (${todo.length})</h2><p class="muted">Спорные и устаревшие значения. Ваше подтверждение обновит дату проверки.</p>
    ${todo.length ? `<table><thead><tr><th>Отель</th><th>Характеристика</th><th>Состояние</th><th>Действие</th></tr></thead><tbody>${todo.slice(0, 60).map(t => { const sp = kinds[t.feature] || {}; return `<tr><td>${esc(t.hotel)}</td><td>${esc(t.title)} <span class="pill">${esc(t.scope_title)}</span><br><small class="muted">${esc(t.explanation || "значение устарело: проверено " + t.observed_at)}</small></td><td>${stBadge(t.explanation ? "disputed" : "stale")}</td><td>${sp.kind === "bool" ? `<button class="quiet cf" data-h="${esc(t.hotel_id)}" data-f="${t.feature}" data-s="${esc(t.scope)}" data-v="true">Да, так</button> <button class="quiet cf" data-h="${esc(t.hotel_id)}" data-f="${t.feature}" data-s="${esc(t.scope)}" data-v="false">Нет</button>` : `<input class="cfn" style="width:90px" type="number" placeholder="значение"> <button class="quiet cf" data-h="${esc(t.hotel_id)}" data-f="${t.feature}" data-s="${esc(t.scope)}">Записать</button>`}</td></tr>`; }).join("")}</tbody></table>` : '<div class="empty">Нечего подтверждать.</div>'}</section>`;
  $("#lo").onclick = () => { state.token = null; sessionStorage.removeItem("lupa_token"); pageHotelier(); };
  $$(".q").forEach(el => $(".send", el).onclick = async () => {
    const t = $("textarea", el).value.trim(); if (!t) return toast("Напишите ответ");
    try { const r = await api(`/api/questions/${el.dataset.q}/answer`, { body: { text: t } }); const out = $(".parsed", el); out.hidden = false;
      out.innerHTML = `<b>Записано как (${r.parsed_by === "llm" ? "LLM" : "правила"}):</b> ` + (r.fields.length ? r.fields.map(f => `${esc(f.scope_title)}: ${f.value === true ? "да" : f.value === false ? "нет" : esc(f.value)}${f.floor_from ? `, с ${f.floor_from}-го этажа` : ""}`).join("; ") : "не удалось выделить значения — уточните ответ"); toast("Ответ записан"); setTimeout(pageHotelier, 2500); } catch (e) { toast(e.message); } });
  $$(".cf").forEach(b => b.onclick = async () => {
    let v = b.dataset.v; if (v === undefined) { const inp = b.parentElement.querySelector(".cfn"); if (!inp.value) return toast("Введите значение"); v = inp.value; } else v = v === "true";
    try { await api("/api/hotelier/confirm", { body: { hotel_id: b.dataset.h, feature: b.dataset.f, scope: b.dataset.s, value: v } }); toast("Записано"); pageHotelier(); } catch (e) { toast(e.message); } });
}

/* ───────── качество и свежесть ───────── */
async function pageQuality() {
  const [m, fr] = await Promise.all([api("/api/metrics"), api("/api/freshness")]);
  const pct = x => Math.round(x * 100) + "%";
  const st = m.by_status, tot = Object.values(st).reduce((a, b) => a + b, 0) || 1;
  const ev = m.evaluation;
  main.innerHTML = `<h1 class="page-h">Качество и свежесть данных</h1>
  <div class="tiles"><div class="tile"><b>${m.hotels}</b><span>отелей</span></div><div class="tile"><b>${m.reviews}</b><span>отзывов проанализировано</span></div><div class="tile"><b>${m.facts_per_hotel}</b><span>фактов на отель из ${m.features} характеристик</span></div>
  <div class="tile"><b>${pct(m.share.confirmed_or_likely)}</b><span>фактов подтверждены или вероятны</span></div><div class="tile"><b>${pct(m.share.multi_source)}</b><span>фактов подтверждены ≥2 источниками</span></div><div class="tile"><b>${pct(m.share.from_geodata)}</b><span>фактов посчитано по геоданным</span></div>
  <div class="tile"><b>${pct(m.share.disputed)}</b><span>фактов — источники спорят</span></div><div class="tile"><b>${pct(m.share.stale)}</b><span>фактов устарели</span></div></div>
  <div class="two"><section class="blk"><h2>Статусы фактов</h2>${Object.keys(STATUS).filter(k => st[k]).map(k => `<div class="hbar"><span>${cert(k)} ${STATUS[k]}</span><div class="tr"><i style="width:${st[k] / tot * 100}%"></i></div><span class="num">${st[k]}</span></div>`).join("")}
  <h3 style="margin-top:14px">Уверенность</h3>${m.confidence_hist.map(b => `<div class="hbar"><span>${b.from}–${b.to}%</span><div class="tr"><i class="second" style="width:${b.count / tot * 100}%"></i></div><span class="num">${b.count}</span></div>`).join("")}</section>
  <section class="blk"><h2>Источники</h2>${m.sources.map(s => `<div class="hbar"><span>${esc(s.title)}</span><div class="tr"><i style="width:${s.observations / Math.max(...m.sources.map(x => x.observations)) * 100}%"></i></div><span class="num">${s.observations}</span></div>`).join("")}
  <h3 style="margin-top:14px">Вопросы отельерам</h3><p>Открыто ${m.questions.open}, отвечено ${m.questions.answered}. Система спрашивает только о том, что не смогла определить сама или где источники спорят.</p></section></div>
  <section class="blk"><h2>Точность на размеченной выборке</h2>${ev.labeled ? `<div class="tiles"><div class="tile"><b>${ev.labeled}</b><span>проверенных значений</span></div>${ev.bool_accuracy != null ? `<div class="tile"><b>${pct(ev.bool_accuracy)}</b><span>верных да/нет (n=${ev.bool_n})</span></div>` : ""}${ev.precision_confident != null ? `<div class="tile"><b>${pct(ev.precision_confident)}</b><span>верных среди «подтверждено/вероятно»</span></div>` : ""}${ev.ece != null ? `<div class="tile"><b>${ev.ece}</b><span>ошибка калибровки (чем меньше, тем лучше)</span></div>` : ""}${ev.numeric_mae != null ? `<div class="tile"><b>${ev.numeric_mae}</b><span>средняя ошибка чисел</span></div>` : ""}</div>${ev.calibration ? `<table><thead><tr><th>Заявленная уверенность</th><th>Фактов</th><th>Средняя уверенность</th><th>Доля верных</th></tr></thead><tbody>${ev.calibration.map(c => `<tr><td>${c.bin}</td><td>${c.n}</td><td>${c.mean_confidence}</td><td>${c.accuracy}</td></tr>`).join("")}</tbody></table>` : ""}` : `<p class="muted">${esc(ev.note)}</p>`}
  <h3 style="margin-top:16px">Разметка</h3><p class="muted">Проверьте значение по фото, карте или по звонку в отель и отметьте, верно ли оно. Сначала показываем самые неуверенные.</p><div id="cands"></div></section>
  <section class="blk"><h2>Свежесть</h2><p>Устарело: <b>${fr.stale_total}</b> · скоро устареет (30 дней): <b>${fr.expiring_total}</b> · спорно: <b>${fr.disputed_total}</b></p>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px"><button id="rs">Пересчитать статусы и перепроверить устаревшее</button><button class="quiet" id="rso">То же, с перекачкой с Островка и OSM</button></div>
  <h3>Последние события</h3><ul class="events">${fr.events.length ? fr.events.map(e => `<li><b>${esc(e.hotel || "")}</b> ${esc(e.text)} <small class="muted">${esc((e.ts || "").slice(0, 16).replace("T", " "))}</small></li>`).join("") : '<li class="muted">Событий пока нет.</li>'}</ul></section>
  <section class="blk"><h2>Покрытие по характеристикам</h2><table><thead><tr><th>Характеристика</th><th>Откуда</th><th>Есть у отелей</th><th>Надёжно</th></tr></thead><tbody>${m.coverage.map(c => `<tr><td>${esc(c.title)}</td><td>${c.computed ? "геоданные + отзывы" : "карточка + отзывы"}${c.builtin ? "" : ' <span class="pill">своя</span>'}</td><td class="num">${pct(c.coverage)}</td><td class="num">${pct(c.reliable)}</td></tr>`).join("")}</tbody></table></section>`;
  const run = online => async () => { try { const r = await api("/api/freshness/refresh", { body: { limit: 10, online } }); toast(`Перепроверено отелей: ${r.refreshed}, изменилось значений: ${r.changes}`); pageQuality(); } catch (e) { toast(e.message); } };
  $("#rs").onclick = run(false); $("#rso").onclick = run(true);
  const cands = await api("/api/eval/candidates");
  $("#cands").innerHTML = cands.length ? `<table><tbody>${cands.slice(0, 12).map((c, i) => `<tr><td>${esc(c.hotel)}</td><td>${esc(c.title)} <span class="pill">${esc(c.scope_title)}</span></td><td>${cert(c.status)} ${esc(c.summary)}</td><td><button class="quiet lb" data-i="${i}" data-ok="1">Верно</button> <button class="quiet lb" data-i="${i}" data-ok="0">Неверно</button></td></tr>`).join("")}</tbody></table>` : '<div class="empty">Нечего размечать.</div>';
  $$(".lb").forEach(b => b.onclick = async () => { const c = cands[+b.dataset.i]; const val = b.dataset.ok === "1" ? !!c.value : !c.value; await api("/api/eval/labels", { body: { hotel_id: c.hotel_id, feature: c.feature, scope: c.scope, value: val } }); toast("Разметка сохранена"); pageQuality(); });
}

/* ───────── характеристики ───────── */
async function pageFeatures() {
  const fs = await api("/api/features");
  const groups = {}; fs.forEach(f => (groups[f.group] ||= []).push(f));
  main.innerHTML = `<h1 class="page-h">Характеристики</h1><p class="muted" style="max-width:70ch">Список расширяется без правки кода: опишите, что для гостей важно, — и система сама найдёт упоминания в отзывах, спросит отельеров и подключит новое к поиску.</p>
  <section class="blk"><h2>Добавить характеристику</h2><label for="fd">Опишите словами</label><textarea id="fd" rows="2" placeholder="Например: в номере есть чайник и можно сделать чай ночью"></textarea>
  <div style="margin:10px 0"><button id="fs">Предложить описание</button></div><form id="ff" hidden>
    <div class="form-row"><div><label for="ft">Название</label><input id="ft" required></div><div><label for="fk">Тип</label><select id="fkind"><option value="bool">Да / нет</option><option value="score">Оценка 0–100</option><option value="number">Число</option></select></div></div>
    <label for="fy">Слова для поиска и отзывов (через запятую, достаточно основ слов)</label><input id="fy">
    <label for="fq">Вопрос отельеру</label><input id="fq"><div style="margin-top:12px"><button type="submit">Добавить и применить к отзывам</button></div></form><div id="fres"></div></section>
  ${Object.entries(groups).map(([g, list]) => `<section class="blk"><h2>${esc(META.groups[g] || g)}</h2><table><tbody>${list.map(f => `<tr><td style="width:240px"><b>${esc(f.title)}</b></td><td>${esc(f.description || "")}<br><small class="muted">${f.computed ? "считается по карте · " : ""}срок жизни ${f.ttl_days} дн.${f.synonyms.length ? " · ищется по: " + esc(f.synonyms.slice(0, 5).join(", ")) : ""}</small></td><td style="width:90px">${f.builtin ? "" : `<button class="quiet del" data-k="${esc(f.key)}">Удалить</button>`}</td></tr>`).join("")}</tbody></table></section>`).join("")}`;
  let draft = null;
  $("#fs").onclick = async () => { const t = $("#fd").value.trim(); if (t.length < 6) return toast("Опишите характеристику хотя бы парой слов"); try { draft = await api("/api/features/suggest", { body: { description: t } }); $("#ff").hidden = false; $("#ft").value = draft.title; $("#fy").value = draft.synonyms.join(", "); $("#fq").value = draft.question; $("#fkind").value = draft.kind; toast(draft.drafted_by === "llm" ? "Описание предложено LLM — проверьте" : "Черновик по ключевым словам — проверьте и поправьте"); } catch (e) { toast(e.message); } };
  $("#ff").onsubmit = async e => { e.preventDefault(); try { const r = await api("/api/features", { body: { ...(draft || {}), description: $("#fd").value.trim(), title: $("#ft").value.trim(), kind: $("#fkind").value, synonyms: $("#fy").value.split(",").map(s => s.trim()).filter(Boolean), question: $("#fq").value.trim() } }); $("#fres").innerHTML = `<div class="parsed">Добавлено «${esc(r.feature.title)}». Упоминания найдены у ${r.applied.hotels_with_evidence} из ${r.applied.hotels} отелей. Теперь по ней работает поиск, и по отелям без данных отельерам уйдут вопросы.</div>`; setTimeout(pageFeatures, 2600); } catch (er) { toast(er.message); } };
  $$(".del").forEach(b => b.onclick = async () => { await api("/api/features/" + b.dataset.k, { method: "DELETE" }); pageFeatures(); });
}

/* ───────── старт ───────── */
(async function boot() {
  state.token = sessionStorage.getItem("lupa_token");
  try { META = await api("/api/meta"); } catch { META = { groups: {}, scopes: {} }; }
  route();
})();
