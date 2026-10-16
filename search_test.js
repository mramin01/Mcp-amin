// Run: node tests/search.test.js [path-to-html]
const { JSDOM } = require("jsdom");
const fs = require("fs");
const htmlPath = process.argv[2] || require("path").join(__dirname, "..", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");
let pass = 0, fail = 0;
const ok = (c, name) => { c ? pass++ : fail++; console.log((c ? "PASS " : "FAIL ") + name); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

const dkProduct = (id, title, rial) => ({ id, title_fa: title, default_variant: { price: { selling_price: rial, rrp_price: rial, discount_percent: 0 }, seller: { title: "دیجی‌کالا" } }, url: { uri: `/product/dkp-${id}/` } });

function makeEnv(mode) {
  const calls = [];
  const fetchStub = async (url, opts = {}) => {
    const u = String(url);
    calls.push(u);
    const sig = opts.signal;
    const abortable = (p) => new Promise((res, rej) => {
      if (sig) sig.addEventListener("abort", () => rej(sig.reason || new Error("aborted")), { once: true });
      p.then(res, rej);
    });
    const isDk = u.includes("api.digikala.com");
    const isTorob = u.includes("api.torob.com");
    if (isDk && mode.digikala) return abortable(mode.digikala(u));
    if (isTorob && mode.torob) return abortable(mode.torob(u));
    // MCP workers and anything else: unavailable
    return abortable(Promise.resolve({ ok: false, status: 503, text: async () => "unavailable", json: async () => ({}) }));
  };
  const dom = new JSDOM(html, {
    runScripts: "dangerously", url: "https://example.test/", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = fetchStub; w.matchMedia = w.matchMedia || (() => ({ matches: false, addListener() {}, addEventListener() {}, removeEventListener() {} })); w.scrollTo = () => {}; w.HTMLElement.prototype.scrollIntoView = () => {}; }
  });
  return { dom, calls, w: dom.window };
}
const json = (obj, status = 200) => Promise.resolve({ ok: status < 400, status, text: async () => JSON.stringify(obj), json: async () => obj });
const dkOK = (n) => () => json({ data: { products: Array.from({ length: n }, (_, i) => dkProduct(1000 + i, "گوشی تست " + i, 150000000 + i * 1000000)) } });
const emptyDk = () => json({ data: { products: [] } });
const emptyTorob = () => json({ results: [] });
const down = () => Promise.resolve({ ok: false, status: 500, text: async () => "err", json: async () => ({}) });

async function search(env, q, wait = 2500) {
  const d = env.w.document;
  d.getElementById("searchInput").value = q;
  env.w.runLiveSearch();
  await sleep(wait);
  return { html: d.getElementById("resultsContainer").innerHTML, status: d.getElementById("sourceStatusBar").textContent, w: env.w };
}

(async () => {
  // ---- normalization unit tests
  {
    const { w } = makeEnv({});
    const n = w.normalizePersianQuery;
    ok(n("ايفون ۱۵  پرو") === "آیفون 15 پرو" || n("ايفون ۱۵  پرو") === "ایفون 15 پرو".replace("ایفون","آیفون"), "normalize: Arabic yeh + Persian digits + spaces");
    ok(n("كتاب") === "کتاب", "normalize: Arabic kaf");
    ok(n("موبایل\u200B ") === "موبایل", "normalize: zero-width space stripped");
    ok(n("ســلام") === "سلام", "normalize: tatweel stripped");
    ok(n("iPhone 15 Pro-Max") === "iPhone 15 Pro-Max", "normalize: Latin model names preserved");
    ok(n("٣٤٥") === "345", "normalize: Arabic-Indic digits");
    ok(n("") === "" && n(null) === "", "normalize: empty/null");
    ok(typeof w.stableUid === "function" && w.stableUid("x", "a", 1) === w.stableUid("x", "a", 1) && w.stableUid("x", "a", 1) !== w.stableUid("x", "b", 1), "stableUid deterministic and distinct");
  }
  // ---- 1. multiple results
  { const e = makeEnv({ digikala: dkOK(3), torob: emptyTorob });
    const r = await search(e, "گوشی");
    ok((r.html.match(/گوشی تست/g) || []).length >= 3, "multi-result: 3 Digikala cards visible");
    ok(!/ارتباط با دیجی‌کالا و ترب برقرار نشد/.test(r.html), "multi-result: no error banner"); }
  // ---- 2. single result
  { const e = makeEnv({ digikala: dkOK(1), torob: emptyTorob });
    const r = await search(e, "گوشی"); ok(/گوشی تست 0/.test(r.html), "single result renders"); }
  // ---- 3. genuine empty (both providers answered, zero items)
  { const e = makeEnv({ digikala: emptyDk, torob: emptyTorob });
    const r = await search(e, "xyzzy123");
    ok(/پیدا نشد/.test(r.html) && !/برقرار نشد/.test(r.html), "genuine empty -> 'not found' state, not outage state");
    ok(/بدون نتیجه/.test(r.status), "status bar says answered-with-no-results"); }
  // ---- 4. all providers failed
  { const e = makeEnv({ digikala: down, torob: down });
    const r = await search(e, "گوشی");
    ok(/برقرار نشد/.test(r.html), "all-failed -> outage state, not 'empty'");
    ok(/پاسخ نداد/.test(r.status), "status bar flags failed providers");
    ok(!/دیجی‌کالا · مشاهده این مدل|تکنولایف · مشاهده این مدل|دیوار · آگهی‌های این مدل/.test(r.html), "no fabricated cross-source cards"); }
  // ---- 5. one provider down, other has results: no fabricated Digikala cards
  { const e = makeEnv({ digikala: down, torob: () => json({ results: [{ prk: "abc", name_fa: "گوشی ترب", price_toman: 9000000, shop_name: "فروشگاه الف", web_client_absolute_url: "/p/abc/x/" }] }) });
    const r = await search(e, "گوشی");
    const items = r.w.eval("state").items;
    ok(items.length === 1 && items[0].source === "torob", "partial outage: only the real Torob item present (no fabricated digikala/technolife/divar items)");
    ok(!items.some(i => i.source === "digikala"), "partial outage: zero Digikala-labelled items"); }
  // ---- 6. malformed response
  { const e = makeEnv({ digikala: () => Promise.resolve({ ok: true, status: 200, text: async () => "<html>blocked</html>" }), torob: () => Promise.resolve({ ok: true, status: 200, text: async () => "not json" }) });
    const r = await search(e, "گوشی"); ok(/برقرار نشد/.test(r.html), "malformed JSON treated as failure, not empty"); }
  // ---- 7. rate limited
  { const e = makeEnv({ digikala: () => json({}, 429), torob: () => json({}, 429) });
    const r = await search(e, "گوشی"); ok(/برقرار نشد/.test(r.html), "429 treated as failure"); }
  // ---- 8. partial item fields (missing image/seller/rating)
  { const e = makeEnv({ digikala: () => json({ data: { products: [{ id: 5, title_fa: "کالای ناقص", default_variant: { price: { selling_price: 5000000 } } }] } }), torob: emptyTorob });
    const r = await search(e, "ناقص"); ok(/کالای ناقص/.test(r.html), "partial fields: item still renders"); }
  // ---- 9. rapid consecutive searches: last wins
  { const e = makeEnv({
      digikala: (u) => /q=slow/.test(decodeURIComponent(u)) ? new Promise(r => setTimeout(() => r(dkOK(2)()), 800)).then(x => x) : dkOK(1)(),
      torob: emptyTorob });
    const d = e.w.document;
    d.getElementById("searchInput").value = "slow"; e.w.runLiveSearch();
    await sleep(50);
    d.getElementById("searchInput").value = "fast"; e.w.runLiveSearch();
    await sleep(2500);
    ok(e.w.eval("state").query === "fast" && e.w.eval("state").items.length === 1, "rapid searches: older response did not overwrite newer"); }
  // ---- 10. empty query does not fire requests
  { const e = makeEnv({ digikala: dkOK(1), torob: emptyTorob });
    e.w.document.getElementById("searchInput").value = "   "; e.w.runLiveSearch(); await sleep(200);
    ok(e.calls.length === 0, "empty query: no network calls"); }
  // ---- 11. query encoding reaches provider normalized
  { const e = makeEnv({ digikala: dkOK(1), torob: emptyTorob });
    await search(e, "ايفون ۱۵");
    ok(e.calls.some(u => u.includes("api.digikala.com") && decodeURIComponent(u).includes("15")), "Persian/Arabic digits normalized in request"); }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
