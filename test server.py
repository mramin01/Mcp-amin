import asyncio, importlib.util, sys, os
HERE=os.path.dirname(os.path.abspath(__file__))
path=sys.argv[1] if len(sys.argv)>1 else os.path.join(HERE,"..","iran_super_shop_mcp.py")
spec=importlib.util.spec_from_file_location("srv",path); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
passed=failed=0
def ok(c,name):
    global passed,failed
    c=bool(c)
    passed+=c; failed+=(not c); print(("PASS " if c else "FAIL ")+name)
def run(coro): return asyncio.run(coro)

# Regression: Torob item without prk crashed the whole search (JS `Math` in Python)
async def fake_worker(endpoint, tool, args=None):
    if tool=="search_products": return {"products":[{"name_fa":"گوشی بدون کلید","price_toman":9000000,"shop_name":"فروشگاه"}]}
    raise RuntimeError("worker down")
async def fake_fetch(url, params=None, use_cache=True): raise RuntimeError("network down")
m.call_remote_mcp_worker=fake_worker; m.fetch_json=fake_fetch
try:
    r=run(m.super_search_all_stores("گوشی",stores=["torob"])); crashed=False
except Exception as e:
    r=None; crashed=True; print("   raised:",type(e).__name__,e)
ok(not crashed,"torob item without prk does not crash search")
if r:
    blob=str(r)
    ok("Math" not in blob,"no JS artefacts in result")
    ok("torob.com/p//" not in blob,"no malformed torob URL for missing prk")

ok(hasattr(m,"safe_int") and m.safe_int("1,234,000")==1234000 and m.safe_int("abc",7)==7 and m.safe_int(None)==0,"safe_int handles strings/garbage")
ok(hasattr(m,"stable_id") and m.stable_id("a",1)==m.stable_id("a",1) and m.stable_id("a",1)!=m.stable_id("b",1),"stable_id deterministic")

# History analysis: too few points must not produce verdicts; provider failure is reported distinctly
async def dead_worker(*a,**k): raise RuntimeError("worker down")
m.call_remote_mcp_worker=dead_worker
r=run(m.analyze_price_history_and_fake_discount("x",1000000,1500000,[1500000,1000000]))
ok(r["status"]=="insufficient_history" and r["is_fake_discount_detected"] is None,"2 points -> no fake-discount verdict")
r=run(m.analyze_price_history_and_fake_discount("x",1000000,1500000,None,digikala_id=1))
ok(r["status"]=="provider_error" and r["provider_errors"],"worker failure reported as provider_error, not 'no data'")
r=run(m.analyze_price_history_and_fake_discount("x",1000000,1500000,[1000000]*6))
ok(r["status"]=="ok" and r["is_fake_discount_detected"] is True,"6 flat points + claimed 33% discount -> fake discount flagged")
r=run(m.analyze_price_history_and_fake_discount("x",800000,1000000,[1000000,1000000,1000000,1000000,1000000,800000]))
ok(r["status"]=="ok" and r["is_fake_discount_detected"] is False,"genuine drop not flagged")
print(f"\n{passed} passed, {failed} failed"); sys.exit(1 if failed else 0)
