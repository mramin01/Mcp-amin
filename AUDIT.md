# Audit - pass 1 (correctness)

| # | Sev | File | Finding | Status |
|---|-----|------|---------|--------|
| 1 | High | index.html | When Digikala/Technolife/Divar returned nothing, `runLiveSearch` copied another store's product (title, price, image) into cards labelled as that store and flagged the source "ok". Fabricated offers. | Fixed (removed) |
| 2 | High | index.html | Provider failure (network/CORS/429/malformed) and a genuine empty result were both returned as `[]`; UI showed one ambiguous message. | Fixed: per-source `responded`/`error`, separate outage vs "not found" states, status pills show failed / no-result |
| 3 | High | iran_super_shop_mcp.py | `Math.random()` (JavaScript) in Python raises `NameError` and aborts `super_search_all_stores` whenever a Torob item has no `prk`. | Fixed |
| 4 | Med | index.html | Normalization ignored zero-width chars, tatweel, diacritics, and Arabic letter variants. | Fixed |
| 5 | Med | index.html | `Math.random()` in item uids breaks saved/compare identity between searches. | Fixed for main search. Still present in food search (~L1866/1878) and deals (~L2416) |
| 6 | Med | server | Price-history verdicts from 2 data points; worker failure reported as "no history". | Fixed: minimum 5 points; `provider_error` status |
| 7 | Med | server | Unguarded `int()` on Torob prices; malformed `torob.com/p//` URL when prk missing. | Fixed |
| 8 | Info | index.html | Browser relies on 3 public CORS proxies (corsproxy.io, allorigins, codetabs) for Digikala/Torob. Privacy and reliability risk and a plausible cause of "search shows nothing". NOT verified (providers unreachable from audit environment). | Open - recommend an HTTP endpoint on the Python server |
| 9 | Info | index.html | No schema validation of provider responses. | Open |

Not yet done: live-provider tests, browser/visual checks, food/cafe/deals flows, redesign pass, review of the sepehr071 and mmdju repos.
