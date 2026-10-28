/* Запасной способ сбора, если Островок не отдаёт страницы скрипту (антибот): выполнить в браузере.
 *
 *  1. Откройте в Chrome https://ostrovok.ru/hotel/russia/st._petersburg/ (любой регион каталога).
 *  2. Откройте консоль (F12 → Console), вставьте весь этот файл и нажмите Enter.
 *  3. Подождите — скрипт ходит по страницам с паузой и в конце скачает lupa-bundle.json.
 *  4. Положите файл в корень репозитория и выполните:  python scripts/import_bundle.py lupa-bundle.json
 *
 * Скрипт читает только публичные страницы, которые вы и так видите, с вашей сессией, и ничего никуда не отправляет:
 * результат сохраняется локальным файлом.
 */
(async () => {
  const LIMIT = window.LUPA_LIMIT || 30, MIN_REVIEWS = 15, DELAY = 1500;
  const region = location.pathname.replace(/^\/hotel\//, "").replace(/\/$/, "").split("/").slice(0, 2).join("/");
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const nextData = html => {
    const m = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
    if (!m) throw new Error("нет __NEXT_DATA__ — возможно, страница антибота");
    return JSON.parse(m[1]).props.pageProps;
  };
  const getText = async url => { const r = await fetch(url, { credentials: "include" }); if (!r.ok) throw new Error(url + " → " + r.status); return r.text(); };
  const out = [], seen = new Set();
  for (let page = 1; out.length < LIMIT && page < 200; page++) {
    let items;
    try { items = (nextData(await getText(`/hotel/${region}/` + (page > 1 ? `?page=${page}` : ""))).serpData || {}).hotels || []; }
    catch (e) { console.warn("каталог", page, e.message); break; }
    if (!items.length) break;
    for (const it of items) {
      if (out.length >= LIMIT) break;
      const id = String(it.masterId);
      if (seen.has(id) || (it.numReviews || 0) < MIN_REVIEWS) continue;
      seen.add(id);
      try {
        await sleep(DELAY);
        const pp = nextData(await getText(`/hotel/${region}/mid${id}/${it.id}/`));
        await sleep(DELAY);
        let api = null;
        try { api = JSON.parse(await getText(`/hotel/search/v1/site/ost_reviews?master_id=${id}&lang=ru`)); } catch (e) { console.warn("отзывы", id, e.message); }
        out.push({
          slug: it.id, region,
          page: { hotel: pp.hotel, reviews: pp.reviews, hotelRegionData: pp.hotelRegionData, datelessRate: pp.datelessRate },
          serp: { datelessRate: it.datelessRate, datefullRate: it.datefullRate, stars: it.stars, nearestBeaches: it.nearestBeaches },
          api_reviews: api,
        });
        console.log(`[${out.length}/${LIMIT}]`, pp.hotel && pp.hotel.name);
      } catch (e) { console.warn("пропуск", id, e.message); }
    }
  }
  const blob = new Blob([JSON.stringify({ schema: 1, exported_at: new Date().toISOString(), hotels: out })], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "lupa-bundle.json"; document.body.appendChild(a); a.click(); a.remove();
  console.log("готово, отелей:", out.length);
})();
