#!/usr/bin/env python3
"""
Iran Super-Shop MCP Server (iran-super-shop-mcp)
================================================
سرور یکپارچه MCP برای جستجوی زنده، مقایسه چندمعیاره، تحلیل تاریخچه قیمت واقعی،
محاسبه اقساط (BNPL) و بهینه‌سازی سبد خرید در فروشگاه‌های ایران.

Author: Mohammad Amin Talebi (@mramin01)
License: MIT
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError:
    httpx = None

SERVER_INSTRUCTIONS = (
    "Unified Iranian E-Commerce Meta-Search, Value/Quality Comparison, "
    "Price History Analyzer & Multi-Store Basket Optimizer MCP Server"
)

try:
    from mcp.server.fastmcp import FastMCP
    _sig = inspect.signature(FastMCP.__init__)
    if "instructions" in _sig.parameters:
        mcp = FastMCP("iran-super-shop-mcp", instructions=SERVER_INSTRUCTIONS)
    elif "description" in _sig.parameters:
        mcp = FastMCP("iran-super-shop-mcp", description=SERVER_INSTRUCTIONS)
    else:
        mcp = FastMCP("iran-super-shop-mcp")
except ImportError:
    class StandaloneStdioMCP:
        """کلاینت/سرور استاندارد JSON-RPC 2.0 روی stdio در صورت عدم نصب پکیج mcp."""

        def __init__(self, name: str, instructions: str = ""):
            self.name = name
            self.instructions = instructions
            self.tools: Dict[str, Dict[str, Any]] = {}

        def tool(self, name: Optional[str] = None, description: Optional[str] = None):
            def decorator(func):
                t_name = name or func.__name__
                self.tools[t_name] = {
                    "func": func,
                    "description": (description or func.__doc__ or "").strip(),
                }
                return func
            return decorator

        async def _handle_rpc(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            method = req.get("method", "")
            req_id = req.get("id")
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": self.name, "version": "2.0.0"},
                        "instructions": self.instructions,
                    },
                }
            if method.startswith("notifications/"):
                return None
            if method == "tools/list":
                tool_list = [
                    {
                        "name": k,
                        "description": v["description"],
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                    for k, v in self.tools.items()
                ]
                return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tool_list}}
            if method == "tools/call":
                params = req.get("params") or {}
                t_name = params.get("name")
                t_args = params.get("arguments") or {}
                if t_name not in self.tools:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"Unknown tool: {t_name}"},
                    }
                try:
                    fn = self.tools[t_name]["func"]
                    res = await fn(**t_args) if inspect.iscoroutinefunction(fn) else fn(**t_args)
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}]
                        },
                    }
                except Exception as exc:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32000, "message": str(exc)},
                    }
            if req_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            return None

        def run(self):
            async def _loop():
                for line in sys.stdin:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        req = json.loads(line)
                        resp = await self._handle_rpc(req)
                        if resp is not None:
                            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                            sys.stdout.flush()
                    except Exception as exc:
                        err_resp = {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": f"Parse error: {exc}"},
                        }
                        sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
                        sys.stdout.flush()

            asyncio.run(_loop())

    mcp = StandaloneStdioMCP("iran-super-shop-mcp", instructions=SERVER_INSTRUCTIONS)


# ============================================================================
# تنظیمات، کش حافظه (TTL Cache)، پروکسی و رجیستری فروشگاه‌ها و ورکرهای MCP
# ============================================================================

PROXY_URL = os.environ.get("IRAN_MCP_PROXY") or os.environ.get("DIGIKALA_MCP_PROXY")
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
}


class TTLCache:
    """کش درون‌حافظه‌ای با زمان انقضا (TTL) برای جلوگیری از درخواست‌های تکراری."""

    def __init__(self, ttl_seconds: float = 180.0, max_entries: int = 256):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._store: Dict[str, Any] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if not entry:
            return None
        expires_at, val = entry
        if time.monotonic() > expires_at:
            self._store.pop(key, None)
            return None
        return val

    def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self.max_entries:
            now = time.monotonic()
            expired = [k for k, (exp, _) in self._store.items() if now > exp]
            for k in expired:
                self._store.pop(k, None)
            if len(self._store) >= self.max_entries:
                oldest_key = next(iter(self._store))
                self._store.pop(oldest_key, None)
        self._store[key] = (time.monotonic() + self.ttl, value)


RESPONSE_CACHE = TTLCache(ttl_seconds=180.0)

MCP_WORKER_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "digikala_worker": {
        "name_fa": "ورکر کلودفلر دیجی‌کالا (mmdju)",
        "mcp_endpoint": "https://digikala-mcp.mmdju.workers.dev/mcp",
        "tools": [
            "search_digikala", "find_best_value", "product_details", "product_variants",
            "product_sellers", "product_price_chart", "product_reviews", "compare_products", "incredible_offers",
        ],
    },
    "torob_worker": {
        "name_fa": "ورکر کلودفلر ترب (mmdju)",
        "mcp_endpoint": "https://torob-mcp.mmdju3.workers.dev/mcp",
        "tools": [
            "search_products", "find_best_value", "product_details", "price_history",
            "product_guide", "compare_products", "special_offers", "shop_profile",
        ],
    },
    "divar_worker": {
        "name_fa": "ورکر کلودفلر دیوار (mmdju)",
        "mcp_endpoint": "https://divar-mcp.mmdju2.workers.dev/mcp",
        "tools": ["search_ads", "find_best_value", "market_price", "ad_details", "compare_ads"],
    },
}

STORE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "digikala": {
        "name_fa": "دیجی‌کالا",
        "domain": "https://www.digikala.com",
        "search_api": "https://api.digikala.com/v1/search/",
        "product_api": "https://api.digikala.com/v2/product/{id}/",
        "mcp_worker": MCP_WORKER_ENDPOINTS["digikala_worker"]["mcp_endpoint"],
        "currency": "rial_to_toman",
        "direct_search_supported": True,
        "supports_installments": ["دیجی‌پی (DigiPay)", "ازکی‌وام", "تارا"],
    },
    "torob": {
        "name_fa": "ترب (موتور مقایسه هزاران فروشگاه)",
        "domain": "https://torob.com",
        "search_api": "https://api.torob.com/v4/base-product/search/",
        "details_api": "https://api.torob.com/v4/base-product/details/",
        "price_chart_api": "https://api.torob.com/v4/base-product/price-chart/",
        "mcp_worker": MCP_WORKER_ENDPOINTS["torob_worker"]["mcp_endpoint"],
        "currency": "toman",
        "direct_search_supported": True,
        "supports_installments": ["بسته به فروشگاه عضو ترب (اسنپ‌پی، دیجی‌پی، تارا)"],
    },
    "technolife": {
        "name_fa": "تکنولایف",
        "domain": "https://www.technolife.com",
        "search_api": "https://www.technolife.com/searchapi",
        "currency": "toman",
        "direct_search_supported": True,
        "supports_installments": ["تکنوپی", "ازکی‌وام", "اسنپ‌پی", "دیجی‌پی"],
    },
    "technolife_store": {
        "name_fa": "تکنولایف و مسترکالا",
        "domain": "https://technolife_store.com",
        "search_api": "https://search.technolife_store.com/ai-engine/api/v2.0/product/search",
        "currency": "rial_to_toman",
        "direct_search_supported": True,
        "supports_installments": ["اعتبار تکنولایف و مسترکالا"],
    },
    "divar": {
        "name_fa": "دیوار",
        "domain": "https://divar.ir",
        "mcp_worker": MCP_WORKER_ENDPOINTS["divar_worker"]["mcp_endpoint"],
        "currency": "toman",
        "direct_search_supported": True,
        "supports_installments": [],
    },
    "snappshop": {
        "name_fa": "اسنپ‌شاپ",
        "domain": "https://snappshop.ir",
        "search_api": "https://api.snappshop.ir/search/v1",
        "currency": "toman",
        "direct_search_supported": False,
        "via_torob_offers": True,
        "supports_installments": ["اسنپ‌پی (۴ قسط بدون کارمزد)"],
    },
    "masterkala": {
        "name_fa": "مسترکالا",
        "domain": "https://www.masterkala.com",
        "search_api": "https://www.masterkala.com/api/v1/products",
        "currency": "toman",
        "direct_search_supported": False,
        "via_torob_offers": True,
        "supports_installments": ["اسنپ‌پی"],
    },
}


# ============================================================================
# توابع کمکی نرمال‌سازی و محاسبه امتیاز بدون داده ساختگی
# ============================================================================

def rial_to_toman(amount: Optional[float | int]) -> int:
    """تبدیل ریال به تومان."""
    if not amount or amount <= 0:
        return 0
    return int(round(float(amount) / 10.0))


def normalize_persian_text(text: str) -> str:
    """یکسان‌سازی حروف فارسی و ارقام."""
    if not text:
        return ""
    replacements = {
        "ي": "ی", "ك": "ک", "ۀ": "ه", "ؤ": "و", "إ": "ا", "أ": "ا",
        "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
        "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return re.sub(r"\s+", " ", text).strip()


def calculate_value_quality_score(
    current_price_toman: int,
    lowest_market_price_toman: int,
    avg_30d_price_toman: Optional[int] = None,
    product_rating: Optional[float] = None,
    review_count: Optional[int] = None,
    seller_score: Optional[float] = None,
    has_official_warranty: Optional[bool] = None,
    shipping_cost_toman: Optional[int] = None,
    is_installment_available: bool = False,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    محاسبه امتیاز ارزش خرید (VQS) صرفاً بر اساس داده‌های واقعی موجود.
    اگر متریکی (مثل امتیاز کاربران یا تاریخچه ۳۰ روزه) از سمت فروشگاه نیامده باشد (None)،
    وزن آن متریک به صورت پویا بین متریک‌های واقعی موجود توزیع می‌شود تا هیچ عدد ساختگی وارد محاسبه نشود.
    """
    base_weights = weights or {
        "price": 0.40,
        "quality": 0.25,
        "seller": 0.18,
        "trend": 0.10,
        "perks": 0.07,
    }

    shipping_val = max(0, shipping_cost_toman) if shipping_cost_toman is not None else 0
    landed_cost = current_price_toman + shipping_val
    ref_low = max(1, lowest_market_price_toman)

    active_scores: Dict[str, float] = {}
    active_weights: Dict[str, float] = {}

    # ۱. امتیاز قیمت (همیشه موجود)
    price_ratio = max(0.0, (landed_cost - ref_low) / ref_low)
    price_score = max(0.0, min(100.0, 100.0 * math.exp(-2.5 * price_ratio)))
    active_scores["price"] = round(price_score, 1)
    active_weights["price"] = base_weights.get("price", 0.40)

    # ۲. امتیاز کیفیت و نظرات (فقط در صورت وجود امتیاز واقعی در منبع)
    if product_rating is not None and product_rating > 0:
        votes = max(1, review_count) if review_count is not None else 5
        prior_rating = 4.0
        prior_votes = 10
        bayesian_rating = (votes * product_rating + prior_votes * prior_rating) / (votes + prior_votes)
        confidence_bonus = min(10.0, math.log10(votes + 1) * 3.5)
        quality_score = min(100.0, (bayesian_rating / 5.0) * 90.0 + confidence_bonus)
        active_scores["quality"] = round(quality_score, 1)
        active_weights["quality"] = base_weights.get("quality", 0.25)

    # ۳. امتیاز فروشنده و گارانتی (فقط در صورت وجود اطلاعات واقعی فروشنده یا گارانتی)
    if seller_score is not None or has_official_warranty is not None:
        s_base = seller_score if seller_score is not None else 80.0
        w_bonus = 15.0 if has_official_warranty is True else 0.0
        seller_norm = min(100.0, max(0.0, s_base * 0.85 + w_bonus))
        active_scores["seller"] = round(seller_norm, 1)
        active_weights["seller"] = base_weights.get("seller", 0.18)

    # ۴. امتیاز تاریخچه قیمت ۳۰ روزه (فقط در صورت وجود سابقه واقعی ۳۰ روزه)
    diff_from_avg_pct: Optional[float] = None
    if avg_30d_price_toman is not None and avg_30d_price_toman > 0:
        diff_from_avg_pct = ((avg_30d_price_toman - current_price_toman) / avg_30d_price_toman) * 100.0
        trend_score = max(0.0, min(100.0, 70.0 + diff_from_avg_pct * 2.5))
        active_scores["trend"] = round(trend_score, 1)
        active_weights["trend"] = base_weights.get("trend", 0.10)
        if diff_from_avg_pct >= 5.0:
            deal_authenticity = "تخفیف واقعی تاییدشده (پایین‌تر از میانگین ۳۰ روزه)"
        elif diff_from_avg_pct <= -5.0:
            deal_authenticity = "بالاتر از میانگین ۳۰ روزه"
        else:
            deal_authenticity = "در محدوده میانگین ۳۰ روزه"
    else:
        deal_authenticity = "فاقد داده تاریخچه ۳۰ روزه (نیازمند استعلام نمودار قیمت)"

    # ۵. امتیاز مزایای ارسال و اقساط
    perks_score = 40.0
    if shipping_cost_toman == 0:
        perks_score += 30.0
    if is_installment_available:
        perks_score += 30.0
    active_scores["perks"] = round(perks_score, 1)
    active_weights["perks"] = base_weights.get("perks", 0.07)

    weight_sum = sum(active_weights.values()) or 1.0
    total_vqs = round(
        sum(active_scores[k] * (active_weights[k] / weight_sum) for k in active_scores),
        1,
    )

    if total_vqs >= 88:
        badge = "🏆 ارزش خرید عالی"
    elif total_vqs >= 76:
        badge = "✅ گزینه مناسب"
    elif total_vqs >= 62:
        badge = "⚖️ گزینه متوسط"
    else:
        badge = "⚠️ نیازمند بررسی بیشتر"

    return {
        "vqs_total": total_vqs,
        "badge": badge,
        "landed_cost_toman": landed_cost,
        "shipping_known": shipping_cost_toman is not None,
        "deal_authenticity": deal_authenticity,
        "vs_30d_avg_pct": round(-diff_from_avg_pct, 1) if diff_from_avg_pct is not None else None,
        "available_metrics": list(active_scores.keys()),
        "breakdown": active_scores,
    }




def build_persian_recommendation_sentence(
    title: str,
    store_name: str,
    price_toman: int,
    vqs_score: float,
    lowest_price_toman: int,
    avg_market_price_toman: int,
    highest_price_toman: int,
    rating: Optional[float] = None,
    review_count: Optional[int] = None,
    discount_pct: int = 0,
    rank: int = 1,
) -> str:
    """
    تولید یک جمله تحلیلی دقیق و مستند برای توضیح دلیل پیشنهاد کالا، امتیاز VQS،
    میزان برتری قیمتی نسبت به سایر فروشگاه‌ها و وضعیت کیفیت.
    """
    savings_vs_max = max(0, highest_price_toman - price_toman)
    savings_vs_avg = max(0, avg_market_price_toman - price_toman)
    pct_below_avg = (
        round((savings_vs_avg / avg_market_price_toman) * 100.0, 1)
        if avg_market_price_toman > 0 and savings_vs_avg > 0
        else 0.0
    )

    parts = [f"امتیاز ارزش خرید {vqs_score} از ۱۰۰ در {store_name}"]

    if price_toman == lowest_price_toman and savings_vs_max > 0:
        parts.append(
            f"دارای ارزان‌ترین قیمت بازار ({ savings_vs_max:,} تومان ارزان‌تر از گران‌ترین فروشنده"
            + (f" و {pct_below_avg}% زیر میانگین بازار)" if pct_below_avg >= 1.0 else ")")
        )
    elif pct_below_avg >= 1.0:
        parts.append(f"قیمت آن {pct_below_avg}% ({savings_vs_avg:,} تومان) ارزان‌تر از میانگین سایر فروشگاه‌هاست")
    else:
        parts.append("دارای قیمت رقابتی و موجودی فعال در بازار")

    if rating is not None and rating > 0:
        votes_txt = f" از {review_count} نظر ثبت‌شده" if review_count else ""
        parts.append(f"با رضایت کیفی {rating} از ۵ ستاره{votes_txt}")

    if discount_pct > 0:
        parts.append(f"همراه با {discount_pct}% تخفیف مستقیم")

    prefix = "🌟 برنده اول مقایسه: " if rank == 1 else "💡 دلیل پیشنهاد: "
    return prefix + "؛ ".join(parts) + "."


# ============================================================================
# کلاینت شبکه غیرهمزمان با سازگاری کامل نسخه‌های httpx، پروکسی و کش
# ============================================================================

def _build_httpx_kwargs(timeout: float = 12.0) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "headers": DEFAULT_HEADERS,
        "timeout": timeout,
        "follow_redirects": True,
    }
    if PROXY_URL and httpx is not None:
        sig = inspect.signature(httpx.AsyncClient.__init__)
        if "proxy" in sig.parameters:
            kwargs["proxy"] = PROXY_URL
        elif "proxies" in sig.parameters:
            kwargs["proxies"] = PROXY_URL
    return kwargs


def _urllib_sync_request(
    url: str,
    method: str = "GET",
    body_bytes: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 12.0,
) -> str:
    req_headers = {**DEFAULT_HEADERS, **(headers or {})}
    handlers = []
    if PROXY_URL:
        handlers.append(urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method=method)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


async def fetch_json(url: str, params: Optional[Dict[str, Any]] = None, use_cache: bool = True) -> Dict[str, Any]:
    """ارسال درخواست GET غیرهمزمان با پشتیبانی از کش TTL و پروکسی در هر دو حالت httpx و urllib."""
    full_url = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    if use_cache:
        cached = RESPONSE_CACHE.get(f"GET:{full_url}")
        if cached is not None:
            return cached

    if httpx is not None:
        async with httpx.AsyncClient(**_build_httpx_kwargs()) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    else:
        raw = await asyncio.to_thread(_urllib_sync_request, full_url, "GET", None, None, 12.0)
        data = json.loads(raw)

    if use_cache:
        RESPONSE_CACHE.set(f"GET:{full_url}", data)
    return data


def _parse_mcp_sse_or_json(raw_text: str) -> Dict[str, Any]:
    trimmed = raw_text.strip()
    rpc_obj = None
    if trimmed.startswith("{"):
        rpc_obj = json.loads(trimmed)
    else:
        for line in reversed(trimmed.splitlines()):
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    candidate = json.loads(payload)
                    if isinstance(candidate, dict) and ("result" in candidate or "error" in candidate):
                        rpc_obj = candidate
                        break
                except Exception:
                    continue
    if not rpc_obj:
        raise ValueError("Invalid MCP response payload")
    if "error" in rpc_obj and rpc_obj["error"]:
        raise RuntimeError(rpc_obj["error"].get("message", "MCP tool error"))
    result = rpc_obj.get("result") or {}
    if "structuredContent" in result and result["structuredContent"]:
        return result["structuredContent"]
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            return json.loads(item["text"])
    return result


async def call_remote_mcp_worker(
    worker_url: str,
    tool_name: str,
    arguments: Dict[str, Any],
    use_cache: bool = True,
) -> Dict[str, Any]:
    """فراخوانی ابزارهای سرورهای MCP کلودفلر (دیجی‌کالا، ترب، دیوار) با کش و پشتیبانی از SSE."""
    cache_key = f"MCP:{worker_url}:{tool_name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
    if use_cache:
        cached = RESPONSE_CACHE.get(cache_key)
        if cached is not None:
            return cached

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    if httpx is not None:
        async with httpx.AsyncClient(**_build_httpx_kwargs(timeout=12.0)) as client:
            resp = await client.post(worker_url, json=payload, headers=headers)
            resp.raise_for_status()
            raw_text = resp.text
    else:
        body_bytes = json.dumps(payload).encode("utf-8")
        raw_text = await asyncio.to_thread(_urllib_sync_request, worker_url, "POST", body_bytes, headers, 12.0)

    parsed = _parse_mcp_sse_or_json(raw_text)
    if use_cache:
        RESPONSE_CACHE.set(cache_key, parsed)
    return parsed


# ============================================================================
# ابزارهای MCP
# ============================================================================

@mcp.tool(
    name="super_search_all_stores",
    description=(
        "جستجوی زنده و همزمان یک کالا در دیجی‌کالا، ترب (شامل فروشندگانی مثل تکنولایف، مسترکالا و...)، "
        "تکنولایف و مسترکالا و دیوار با گزارش شفاف خطاهای شبکه و بدون هیچ‌گونه قیمت یا امتیاز ساختگی."
    ),
)
async def super_search_all_stores(
    query: str,
    priority: str = "best_value",  # 'best_value' | 'cheapest' | 'highest_quality' | 'installment'
    min_price_toman: Optional[int] = None,
    max_price_toman: Optional[int] = None,
    only_official_warranty: bool = False,
    stores: Optional[List[str]] = None,
) -> Dict[str, Any]:
    norm_query = normalize_persian_text(query)
    supported_direct = {"digikala", "torob", "technolife_store", "divar"}
    requested_stores = [s.lower().strip() for s in (stores or ["digikala", "torob", "technolife_store"])]
    unsupported_stores = [s for s in requested_stores if s not in supported_direct]
    active_stores = [s for s in requested_stores if s in supported_direct]

    weight_profiles = {
        "best_value": {"price": 0.40, "quality": 0.25, "seller": 0.18, "trend": 0.10, "perks": 0.07},
        "cheapest": {"price": 0.70, "quality": 0.10, "seller": 0.10, "trend": 0.05, "perks": 0.05},
        "highest_quality": {"price": 0.15, "quality": 0.45, "seller": 0.30, "trend": 0.05, "perks": 0.05},
        "installment": {"price": 0.25, "quality": 0.20, "seller": 0.15, "trend": 0.10, "perks": 0.30},
    }
    weights = weight_profiles.get(priority, weight_profiles["best_value"])

    results: List[Dict[str, Any]] = []
    source_errors: Dict[str, str] = {}

    # ۱. دیجی‌کالا (ابتدا از طریق ورکر کلودفلر، و در صورت خطا از طریق API مستقیم دیجی‌کالا)
    if "digikala" in active_stores:
        try:
            dk_args: Dict[str, Any] = {"query": norm_query, "only_marketable": True, "limit": 8}
            if min_price_toman is not None:
                dk_args["min_price_toman"] = min_price_toman
            if max_price_toman is not None:
                dk_args["max_price_toman"] = max_price_toman
            dk_mcp = await call_remote_mcp_worker(
                MCP_WORKER_ENDPOINTS["digikala_worker"]["mcp_endpoint"],
                "search_digikala",
                dk_args,
            )
            for it in (dk_mcp.get("items") or [])[:8]:
                selling_price = int(it.get("price_toman") or rial_to_toman(it.get("price_rial", 0)))
                if selling_price <= 0:
                    continue
                results.append({
                    "store_id": "digikala",
                    "store_name": "دیجی‌کالا",
                    "product_id": f"dk-{it.get('id')}",
                    "title_fa": it.get("title", ""),
                    "price_toman": selling_price,
                    "price_before_discount_toman": it.get("price_before_toman"),
                    "discount_pct": it.get("discount_percent", 0),
                    "avg_30d_price_toman": None,
                    "rating": float(it["rating_stars"]) if it.get("rating_stars") is not None else None,
                    "review_count": int(it["rating_count"]) if it.get("rating_count") is not None else None,
                    "seller_name": it.get("seller") or "دیجی‌کالا",
                    "seller_score": None,
                    "warranty": None,
                    "has_official_warranty": None,
                    "shipping_cost_toman": None,
                    "installment_options": STORE_REGISTRY["digikala"]["supports_installments"],
                    "url": it.get("url") or f"https://www.digikala.com/product/dkp-{it.get('id')}/",
                    "image_url": it.get("image") or "",
                })
        except Exception as exc_worker:
            try:
                dk_data = await fetch_json(
                    STORE_REGISTRY["digikala"]["search_api"],
                    params={"q": norm_query, "page": 1},
                )
                for item in (dk_data.get("data") or {}).get("products", [])[:8]:
                    default_variant = item.get("default_variant") or {}
                    price_info = default_variant.get("price") or {}
                    selling_price = rial_to_toman(price_info.get("selling_price", 0))
                    rrp_price = rial_to_toman(price_info.get("rrp_price", selling_price))
                    if selling_price <= 0:
                        continue
                    rating_obj = item.get("rating") or {}
                    rating_raw = (rating_obj.get("rate") / 20.0) if rating_obj.get("rate") is not None else None
                    rating_count = rating_obj.get("count")
                    seller = default_variant.get("seller") or {}
                    seller_stars = (seller.get("rating") or {}).get("total_rate")
                    warranty_title = (default_variant.get("warranty") or {}).get("title_fa")
                    has_official = bool(
                        warranty_title and ("۱۸ ماه" in warranty_title or "شرکتی" in warranty_title or "رسمی" in warranty_title)
                    )
                    main_urls = ((item.get("images") or {}).get("main") or {}).get("url") or []
                    image_url = main_urls[0] if isinstance(main_urls, list) and len(main_urls) > 0 else ""

                    results.append({
                        "store_id": "digikala",
                        "store_name": "دیجی‌کالا",
                        "product_id": f"dk-{item.get('id')}",
                        "title_fa": item.get("title_fa", ""),
                        "price_toman": selling_price,
                        "price_before_discount_toman": rrp_price if rrp_price > selling_price else None,
                        "discount_pct": price_info.get("discount_percent", 0),
                        "avg_30d_price_toman": None,
                        "rating": round(rating_raw, 2) if rating_raw is not None else None,
                        "review_count": int(rating_count) if rating_count is not None else None,
                        "seller_name": seller.get("title") or "دیجی‌کالا",
                        "seller_score": float(seller_stars) if seller_stars is not None else None,
                        "warranty": warranty_title,
                        "has_official_warranty": has_official if warranty_title else None,
                        "shipping_cost_toman": None,
                        "installment_options": STORE_REGISTRY["digikala"]["supports_installments"],
                        "url": f"https://www.digikala.com/product/dkp-{item.get('id')}/",
                        "image_url": image_url,
                    })
            except Exception as exc_direct:
                source_errors["digikala"] = f"Worker error: {exc_worker} | Direct API error: {exc_direct}"

    # ۲. ترب (از طریق ورکر کلودفلر ترب یا API مستقیم ترب، بدون هیچ امتیاز ساختگی)
    if "torob" in active_stores:
        try:
            tr_args: Dict[str, Any] = {"query": norm_query, "available_only": True, "limit": 8}
            if min_price_toman is not None:
                tr_args["min_price_toman"] = min_price_toman
            if max_price_toman is not None:
                tr_args["max_price_toman"] = max_price_toman
            tr_mcp = await call_remote_mcp_worker(
                MCP_WORKER_ENDPOINTS["torob_worker"]["mcp_endpoint"],
                "search_products",
                tr_args,
            )
            for item in (tr_mcp.get("products") or [])[:8]:
                price_toman = int(item.get("price_toman") or item.get("price") or 0)
                if price_toman <= 0:
                    continue
                prk = item.get("prk") or item.get("random_key") or ""
                results.append({
                    "store_id": "torob",
                    "store_name": f"ترب ({item.get('shop_name') or 'ارزان‌ترین فروشنده'})",
                    "product_id": f"tr-{prk}",
                    "title_fa": item.get("name_fa") or item.get("name1") or "",
                    "price_toman": price_toman,
                    "price_before_discount_toman": None,
                    "discount_pct": 0,
                    "avg_30d_price_toman": None,
                    "rating": None,
                    "review_count": None,
                    "seller_name": item.get("shop_name") or "فروشنده عضو ترب",
                    "seller_score": None,
                    "warranty": None,
                    "has_official_warranty": None,
                    "shipping_cost_toman": None,
                    "installment_options": STORE_REGISTRY["torob"]["supports_installments"],
                    "url": item.get("url") or f"https://torob.com/p/{prk}/",
                    "image_url": item.get("image") or item.get("image_url") or "",
                })
        except Exception as exc_tr:
            source_errors["torob"] = str(exc_tr)

    # ۳. تکنولایف و مسترکالا (استعلام زنده از موتور جستجوی تکنولایف و مسترکالا)
    if "technolife_store" in active_stores:
        try:
            bs_data = await fetch_json(
                STORE_REGISTRY["technolife_store"]["search_api"],
                params={"q": norm_query, "from": 0, "size": 6},
            )
            for bp in (bs_data.get("products") or [])[:6]:
                price_toman = rial_to_toman(bp.get("price", 0))
                if price_toman <= 0:
                    continue
                old_toman = rial_to_toman(bp.get("primaryPrice", 0)) or None
                disc_pct = (
                    int(round(((old_toman - price_toman) / old_toman) * 100))
                    if old_toman and old_toman > price_toman
                    else 0
                )
                r_obj = bp.get("rating") or {}
                vendor = bp.get("vendor") or {}
                photo = bp.get("photo") or {}
                results.append({
                    "store_id": "technolife_store",
                    "store_name": "تکنولایف و مسترکالا",
                    "product_id": f"bs-{bp.get('id')}",
                    "title_fa": bp.get("name", ""),
                    "price_toman": price_toman,
                    "price_before_discount_toman": old_toman,
                    "discount_pct": disc_pct,
                    "avg_30d_price_toman": None,
                    "rating": float(r_obj["average"]) if r_obj.get("average") is not None else None,
                    "review_count": int(r_obj["count"]) if r_obj.get("count") is not None else None,
                    "seller_name": vendor.get("name") or "غرفه تکنولایف و مسترکالا",
                    "seller_score": None,
                    "warranty": None,
                    "has_official_warranty": None,
                    "shipping_cost_toman": None,
                    "installment_options": STORE_REGISTRY["technolife_store"]["supports_installments"],
                    "url": f"https://technolife_store.com/{vendor.get('identifier') or 's'}/product/{bp.get('id')}",
                    "image_url": photo.get("MEDIUM") or photo.get("SMALL") or "",
                })
        except Exception as exc_bs:
            source_errors["technolife_store"] = str(exc_bs)

    # ۴. دیوار (در صورت درخواست در لیست stores)
    if "divar" in active_stores:
        try:
            dv_mcp = await call_remote_mcp_worker(
                MCP_WORKER_ENDPOINTS["divar_worker"]["mcp_endpoint"],
                "search_ads",
                {"query": norm_query, "city": "tehran", "has_photo": True, "limit": 6},
            )
            for ad in (dv_mcp.get("items") or [])[:6]:
                price_toman = int(ad.get("price_toman") or 0)
                if price_toman <= 0:
                    continue
                results.append({
                    "store_id": "divar",
                    "store_name": "دیوار تهران",
                    "product_id": f"dv-{ad.get('token')}",
                    "title_fa": ad.get("title", ""),
                    "price_toman": price_toman,
                    "price_before_discount_toman": None,
                    "discount_pct": 0,
                    "avg_30d_price_toman": None,
                    "rating": None,
                    "review_count": None,
                    "seller_name": ad.get("district") or "آگهی‌دهنده دیوار",
                    "seller_score": None,
                    "warranty": None,
                    "has_official_warranty": None,
                    "shipping_cost_toman": None,
                    "installment_options": [],
                    "url": ad.get("url") or f"https://divar.ir/v/{ad.get('token')}",
                    "image_url": ad.get("image") or "",
                })
        except Exception as exc_dv:
            source_errors["divar"] = str(exc_dv)

    # اعمال یکپارچه فیلتر گارانتی رسمی روی تمام فروشگاه‌ها
    if only_official_warranty:
        results = [r for r in results if r.get("has_official_warranty") is True]

    # اعمال فیلتر بازه قیمتی
    if min_price_toman is not None:
        results = [r for r in results if r["price_toman"] >= min_price_toman]
    if max_price_toman is not None:
        results = [r for r in results if r["price_toman"] <= max_price_toman]

    if results:
        lowest_price = min(
            r["price_toman"] + (r["shipping_cost_toman"] or 0) for r in results
        )
        for r in results:
            r["smart_evaluation"] = calculate_value_quality_score(
                current_price_toman=r["price_toman"],
                lowest_market_price_toman=lowest_price,
                avg_30d_price_toman=r.get("avg_30d_price_toman"),
                product_rating=r.get("rating"),
                review_count=r.get("review_count"),
                seller_score=r.get("seller_score"),
                has_official_warranty=r.get("has_official_warranty"),
                shipping_cost_toman=r.get("shipping_cost_toman"),
                is_installment_available=bool(r.get("installment_options")),
                weights=weights,
            )

        if priority == "cheapest":
            results.sort(key=lambda x: x["smart_evaluation"]["landed_cost_toman"])
        elif priority == "highest_quality":
            results.sort(
                key=lambda x: (
                    x.get("rating") is not None,
                    x["smart_evaluation"]["breakdown"].get("quality", 0.0),
                    x["smart_evaluation"]["vqs_total"],
                ),
                reverse=True,
            )
        else:
            results.sort(key=lambda x: x["smart_evaluation"]["vqs_total"], reverse=True)

        all_prices = [r["price_toman"] for r in results if r["price_toman"] > 0]
        avg_market = int(round(sum(all_prices) / len(all_prices))) if all_prices else lowest_price
        max_market = max(all_prices) if all_prices else lowest_price
        for idx, r in enumerate(results, start=1):
            r["recommendation_reason"] = build_persian_recommendation_sentence(
                title=r.get("title_fa", ""),
                store_name=r.get("store_name", ""),
                price_toman=r["price_toman"],
                vqs_score=r["smart_evaluation"]["vqs_total"],
                lowest_price_toman=lowest_price,
                avg_market_price_toman=avg_market,
                highest_price_toman=max_market,
                rating=r.get("rating"),
                review_count=r.get("review_count"),
                discount_pct=int(r.get("discount_pct") or 0),
                rank=idx,
            )

    return {
        "query": norm_query,
        "priority_mode": priority,
        "searched_stores": active_stores,
        "unsupported_stores": unsupported_stores,
        "source_errors": source_errors,
        "total_offers_found": len(results),
        "best_pick": results[0] if results else None,
        "cheapest_pick": min(results, key=lambda x: x["price_toman"]) if results else None,
        "offers": results,
    }


@mcp.tool(
    name="compare_products_deep",
    description=(
        "مقایسه عمیق و چندمعیاره حداقل ۲ کالا با اعمال واقعی وزن‌های ترجیح کاربر "
        "('balanced'، 'budget'، 'performance'، 'longevity')."
    ),
)
async def compare_products_deep(
    items: List[Dict[str, Any]],
    user_preference: str = "balanced",  # 'balanced' | 'budget' | 'performance' | 'longevity'
) -> Dict[str, Any]:
    if not items or len(items) < 2:
        return {"error": "حداقل دو کالا برای مقایسه وارد کنید."}

    valid_items = [i for i in items if isinstance(i.get("price_toman"), (int, float)) and i["price_toman"] > 0]
    if len(valid_items) < 2:
        return {"error": "حداقل دو کالا باید دارای فیلد معتبر و مثبت price_toman باشند."}

    preference_weights = {
        "balanced": {"price": 0.38, "quality": 0.25, "seller": 0.20, "trend": 0.10, "perks": 0.07},
        "budget": {"price": 0.65, "quality": 0.12, "seller": 0.10, "trend": 0.08, "perks": 0.05},
        "performance": {"price": 0.15, "quality": 0.50, "seller": 0.25, "trend": 0.05, "perks": 0.05},
        "longevity": {"price": 0.15, "quality": 0.35, "seller": 0.40, "trend": 0.05, "perks": 0.05},
    }
    weights = preference_weights.get(user_preference, preference_weights["balanced"])

    lowest_price = min(int(i["price_toman"]) for i in valid_items)
    enriched = []
    for item in valid_items:
        price = int(item["price_toman"])
        vqs = calculate_value_quality_score(
            current_price_toman=price,
            lowest_market_price_toman=lowest_price,
            avg_30d_price_toman=int(item["avg_30d_price_toman"]) if item.get("avg_30d_price_toman") else None,
            product_rating=float(item["rating"]) if item.get("rating") is not None else None,
            review_count=int(item["review_count"]) if item.get("review_count") is not None else None,
            seller_score=float(item["seller_score"]) if item.get("seller_score") is not None else None,
            has_official_warranty=item.get("has_official_warranty"),
            shipping_cost_toman=int(item["shipping_cost_toman"]) if item.get("shipping_cost_toman") is not None else None,
            is_installment_available=bool(item.get("installment_options")),
            weights=weights,
        )
        enriched.append({**item, "smart_evaluation": vqs})

    enriched.sort(key=lambda x: x["smart_evaluation"]["vqs_total"], reverse=True)
    winner = enriched[0]
    cheapest = min(enriched, key=lambda x: x["smart_evaluation"]["landed_cost_toman"])

    return {
        "user_preference": user_preference,
        "applied_weights": weights,
        "recommended_winner": {
            "title": winner.get("title_fa"),
            "store": winner.get("store_name"),
            "vqs_score": winner["smart_evaluation"]["vqs_total"],
            "reason": (
                f"کسب بالاترین امتیاز ({winner['smart_evaluation']['vqs_total']} از 100) "
                f"بر اساس پروفایل ترجیح «{user_preference}»."
            ),
        },
        "budget_winner": {
            "title": cheapest.get("title_fa"),
            "store": cheapest.get("store_name"),
            "landed_cost_toman": cheapest["smart_evaluation"]["landed_cost_toman"],
        },
        "comparison_table": enriched,
    }


@mcp.tool(
    name="analyze_price_history_and_fake_discount",
    description=(
        "بررسی تاریخچه قیمت واقعی ۳۰ روزه کالا (از طریق نقاط ارسالی یا استعلام زنده از دیجی‌کالا/ترب با شناسه کالا) "
        "برای تشخیص تخفیف واقعی در برابر تخفیف صوری."
    ),
)
async def analyze_price_history_and_fake_discount(
    product_title: str,
    current_price_toman: int,
    claimed_previous_price_toman: Optional[int] = None,
    price_history_points: Optional[List[int]] = None,
    digikala_id: Optional[int] = None,
    torob_prk: Optional[str] = None,
) -> Dict[str, Any]:
    history = [int(p) for p in (price_history_points or []) if isinstance(p, (int, float)) and p > 0]

    # در صورت عدم ارسال نقاط تاریخچه، تلاش برای استعلام زنده از سرورهای MCP دیجی‌کالا یا ترب
    if len(history) < 2 and digikala_id:
        try:
            chart = await call_remote_mcp_worker(
                MCP_WORKER_ENDPOINTS["digikala_worker"]["mcp_endpoint"],
                "product_price_chart",
                {"id": int(digikala_id)},
            )
            for pt in chart.get("points") or []:
                if pt.get("price_toman"):
                    history.append(int(pt["price_toman"]))
        except Exception:
            pass

    if len(history) < 2 and torob_prk:
        try:
            thist = await call_remote_mcp_worker(
                MCP_WORKER_ENDPOINTS["torob_worker"]["mcp_endpoint"],
                "price_history",
                {"prk": torob_prk},
            )
            for s in thist.get("series") or []:
                for pt in s.get("points") or []:
                    val = pt.get("price_toman") or pt.get("price") or (pt[1] if isinstance(pt, list) and len(pt) > 1 else None)
                    if isinstance(val, (int, float)) and val > 0:
                        history.append(int(val))
        except Exception:
            pass

    if len(history) < 2:
        return {
            "status": "insufficient_history",
            "product_title": product_title,
            "current_price_toman": current_price_toman,
            "data_points_count": len(history),
            "is_fake_discount_detected": None,
            "verdict": (
                "❓ داده تاریخچه قیمت ۳۰ روزه برای این کالا ارسال یا یافت نشد؛ "
                "بدون حداقل ۲ نقطه قیمتی تاریخی، امکان تایید تخفیف واقعی یا کف قیمت وجود ندارد."
            ),
            "buy_timing_recommendation": "لطفاً شناسه دیجی‌کالا (digikala_id)، شناسه ترب (torob_prk) یا نقاط تاریخچه قیمت (price_history_points) را ارسال کنید.",
        }

    min_30d = min(history)
    max_30d = max(history)
    avg_30d = int(round(sum(history) / len(history)))

    claimed_discount_pct = 0.0
    if claimed_previous_price_toman and claimed_previous_price_toman > current_price_toman:
        claimed_discount_pct = round(
            ((claimed_previous_price_toman - current_price_toman) / claimed_previous_price_toman) * 100.0, 1
        )

    real_discount_vs_avg_pct = round(((avg_30d - current_price_toman) / max(1, avg_30d)) * 100.0, 1)
    is_fake_discount = bool(claimed_discount_pct > 10.0 and current_price_toman >= avg_30d * 0.98)

    if is_fake_discount:
        verdict = "🚨 هشدار تخفیف صوری: قیمت قبل از تخفیف غیرواقعی است و قیمت فعلی برابر یا بالاتر از میانگین دوره است."
        buy_timing = "صبر کنید یا از فروشنده دیگری بخرید"
    elif current_price_toman <= min_30d * 1.02 and min_30d < max_30d:
        verdict = "💎 در کف قیمت تاریخی ثبت‌شده: زمان مناسب برای خرید."
        buy_timing = "زمان مناسب خرید (در محدوده کف قیمت ثبت‌شده)"
    elif real_discount_vs_avg_pct >= 5.0:
        verdict = f"✅ تخفیف واقعی: {real_discount_vs_avg_pct}% ارزان‌تر از میانگین ثبت‌شده."
        buy_timing = "زمان مناسب برای خرید"
    else:
        verdict = "⚖️ قیمت عادی بازار: در محدوده میانگین تاریخی قرار دارد."
        buy_timing = "قیمت متعادل است"

    return {
        "status": "ok",
        "product_title": product_title,
        "current_price_toman": current_price_toman,
        "data_points_count": len(history),
        "min_30d_toman": min_30d,
        "avg_30d_toman": avg_30d,
        "max_30d_toman": max_30d,
        "claimed_discount_pct": claimed_discount_pct,
        "real_discount_vs_30d_avg_pct": real_discount_vs_avg_pct,
        "is_fake_discount_detected": is_fake_discount,
        "verdict": verdict,
        "buy_timing_recommendation": buy_timing,
    }


@mcp.tool(
    name="calculate_installment_and_bnpl_plans",
    description=(
        "محاسبه و مقایسه شرایط خرید اقساطی (BNPL) با امکان تنظیم نرخ کارمزد و نرخ تورم ماهانه (شامل تورم ۰٪)."
    ),
)
async def calculate_installment_and_bnpl_plans(
    cash_price_toman: int,
    monthly_inflation_rate_pct: float = 3.0,
    six_month_total_fee_pct: float = 11.5,
    twelve_month_total_fee_pct: float = 23.0,
) -> Dict[str, Any]:
    if cash_price_toman <= 0:
        return {"error": "قیمت نقدی کالا باید بزرگ‌تر از صفر باشد."}

    r = max(0.0, float(monthly_inflation_rate_pct)) / 100.0
    fee6 = max(0.0, float(six_month_total_fee_pct))
    fee12 = max(0.0, float(twelve_month_total_fee_pct))

    plans = [
        {
            "provider": "طرح ۴ قسطه بدون کارمزد (اسنپ‌پی / تارا)",
            "months": 4,
            "upfront_toman": int(round(cash_price_toman * 0.25)),
            "monthly_installment_toman": int(round(cash_price_toman * 0.25)),
            "total_repayment_toman": cash_price_toman,
            "estimated_sample_fee_pct": 0.0,
            "supported_stores": ["اسنپ‌شاپ", "تکنولایف", "مسترکالا", "خانومی", "شاپینو"],
        },
        {
            "provider": "طرح اعتباری ۶ ماهه (نمونه با کارمزد قابل تنظیم)",
            "months": 6,
            "upfront_toman": 0,
            "monthly_installment_toman": int(round((cash_price_toman * (1.0 + fee6 / 100.0)) / 6.0)),
            "total_repayment_toman": int(round(cash_price_toman * (1.0 + fee6 / 100.0))),
            "estimated_sample_fee_pct": fee6,
            "supported_stores": ["دیجی‌کالا", "تکنولایف"],
        },
        {
            "provider": "طرح اعتباری ۱۲ ماهه (نمونه با کارمزد قابل تنظیم)",
            "months": 12,
            "upfront_toman": 0,
            "monthly_installment_toman": int(round((cash_price_toman * (1.0 + fee12 / 100.0)) / 12.0)),
            "total_repayment_toman": int(round(cash_price_toman * (1.0 + fee12 / 100.0))),
            "estimated_sample_fee_pct": fee12,
            "supported_stores": ["تکنولایف", "دیجی‌کالا"],
        },
    ]

    for p in plans:
        npv = float(p["upfront_toman"])
        n_rem = p["months"] - (1 if p["upfront_toman"] > 0 else 0)
        for m in range(1, n_rem + 1):
            npv += p["monthly_installment_toman"] / ((1.0 + r) ** m)
        p["inflation_adjusted_real_cost_toman"] = int(round(npv))
        p["real_savings_vs_cash_toman"] = cash_price_toman - p["inflation_adjusted_real_cost_toman"]

    plans.sort(key=lambda x: x["inflation_adjusted_real_cost_toman"])
    return {
        "cash_price_toman": cash_price_toman,
        "assumed_monthly_inflation_pct": monthly_inflation_rate_pct,
        "disclaimer": (
            "نرخ کارمزد و سود طرح‌های ۶ و ۱۲ ماهه بر اساس مقادیر نمونه محاسبه شده است و "
            "بسته به شرکت ارائه‌دهنده اعتبار (دیجی‌پی، ازکی‌وام، تکنوپی) و رتبه اعتباری کاربر متغیر است."
        ),
        "best_financial_strategy": plans[0]["provider"],
        "plans": plans,
    }


@mcp.tool(
    name="optimize_multi_store_basket",
    description=(
        "بهینه‌ساز سبد خرید چندکالایی: بررسی دقیق خرید تفکیکی (Split Basket) در برابر خرید یکجا از یک فروشگاه "
        "(فقط برای فروشگاه‌هایی که تمام اقلام سبد را موجود دارند) با اعمال یکپارچه سقف ارسال رایگان."
    ),
)
async def optimize_multi_store_basket(
    basket_items: List[Dict[str, Any]],
    free_shipping_threshold_toman: int = 1000000,
    default_shipping_toman: int = 45000,
) -> Dict[str, Any]:
    if not basket_items:
        return {"error": "لیست اقلام سبد خرید خالی است."}

    store_totals: Dict[str, int] = {}
    store_item_counts: Dict[str, int] = {}
    store_base_shipping: Dict[str, int] = {}

    split_choice: List[Dict[str, Any]] = []
    split_store_subtotals: Dict[str, int] = {}
    valid_items_count = 0

    for item in basket_items:
        name = item.get("item_name") or "کالا"
        raw_offers = item.get("offers") or []
        valid_offers = [
            o for o in raw_offers
            if isinstance(o, dict)
            and o.get("store")
            and isinstance(o.get("price_toman"), (int, float))
            and int(o["price_toman"]) > 0
        ]
        if not valid_offers:
            continue

        valid_items_count += 1
        best_offer = min(valid_offers, key=lambda o: int(o["price_toman"]))
        best_store = str(best_offer["store"])
        best_price = int(best_offer["price_toman"])

        split_store_subtotals[best_store] = split_store_subtotals.get(best_store, 0) + best_price
        store_base_shipping[best_store] = int(best_offer.get("shipping_toman", default_shipping_toman))

        split_choice.append({
            "item_name": name,
            "chosen_store": best_store,
            "price_toman": best_price,
        })

        seen_stores_for_item = set()
        for off in valid_offers:
            s_name = str(off["store"])
            if s_name in seen_stores_for_item:
                continue
            seen_stores_for_item.add(s_name)
            p_val = int(off["price_toman"])
            store_totals[s_name] = store_totals.get(s_name, 0) + p_val
            store_item_counts[s_name] = store_item_counts.get(s_name, 0) + 1
            if s_name not in store_base_shipping:
                store_base_shipping[s_name] = int(off.get("shipping_toman", default_shipping_toman))

    if valid_items_count == 0:
        return {"error": "هیچ پیشنهاد معتبری با price_toman بزرگ‌تر از صفر در سبد یافت نشد."}

    # محاسبه هزینه ارسال در حالت Split با اعمال دقیق همان قانون ارسال رایگان بالای سقف خرید برای هر فروشگاه
    split_subtotal = sum(split_store_subtotals.values())
    split_shipping_breakdown: Dict[str, int] = {}
    for st, st_sub in split_store_subtotals.items():
        base_ship = store_base_shipping.get(st, default_shipping_toman)
        split_shipping_breakdown[st] = 0 if st_sub >= free_shipping_threshold_toman else base_ship

    split_shipping = sum(split_shipping_breakdown.values())
    split_grand_total = split_subtotal + split_shipping

    # فقط فروشگاه‌هایی که ۱۰۰٪ اقلام معتبر سبد را دارند در مقایسه تک‌فروشگاهی شرکت می‌کنند
    complete_single_stores = []
    partial_stores = []
    for s_name, subtotal in store_totals.items():
        count = store_item_counts.get(s_name, 0)
        base_ship = store_base_shipping.get(s_name, default_shipping_toman)
        ship = 0 if subtotal >= free_shipping_threshold_toman else base_ship
        entry = {
            "store": s_name,
            "covered_items": count,
            "total_basket_items": valid_items_count,
            "subtotal_toman": subtotal,
            "shipping_toman": ship,
            "grand_total_toman": subtotal + ship,
        }
        if count == valid_items_count:
            complete_single_stores.append(entry)
        else:
            partial_stores.append(entry)

    complete_single_stores.sort(key=lambda x: x["grand_total_toman"])
    best_single = complete_single_stores[0] if complete_single_stores else None

    recommend_split = True
    if best_single and best_single["grand_total_toman"] <= split_grand_total:
        recommend_split = False

    return {
        "recommended_strategy": (
            "خرید تفکیکی از چند فروشگاه (Split Basket)"
            if recommend_split
            else f"خرید یکجا از {best_single['store']} (Single Store)"
        ),
        "split_basket_summary": {
            "items": split_choice,
            "store_subtotals_toman": split_store_subtotals,
            "store_shipping_toman": split_shipping_breakdown,
            "subtotal_toman": split_subtotal,
            "total_shipping_toman": split_shipping,
            "grand_total_toman": split_grand_total,
        },
        "best_single_store_summary": best_single,
        "incomplete_stores_excluded_from_single_winner": partial_stores,
        "net_savings_toman": (
            abs(best_single["grand_total_toman"] - split_grand_total)
            if best_single
            else 0
        ),
    }


@mcp.tool(
    name="snapp_food_and_market_true_cost",
    description=(
        "مقایسه هزینه واقعی نهایی سفارش در اسنپ‌مارکت، اسنپ‌فود و اکالا شامل قیمت اقلام، "
        "بسته‌بندی، پیک، حداقل سفارش و کد تخفیف."
    ),
)
async def snapp_food_and_market_true_cost(
    items_total_toman: int,
    stores: List[Dict[str, Any]],
) -> Dict[str, Any]:
    evaluated = []
    for st in stores:
        discount = int(st.get("discount_toman", 0))
        packaging = int(st.get("packaging_fee_toman", 0))
        delivery = int(st.get("delivery_fee_toman", 0))
        coupon = int(st.get("coupon_toman", 0))
        min_order = int(st.get("min_order_toman", 0))
        net_items = max(0, items_total_toman - discount)
        true_total = max(0, net_items + packaging + delivery - coupon)
        evaluated.append({
            "store_name": st.get("store_name", "فروشگاه"),
            "platform": st.get("platform", "اسنپ‌مارکت / اکالا"),
            "meets_min_order": net_items >= min_order,
            "shortfall_to_min_order_toman": max(0, min_order - net_items),
            "true_final_cost_toman": true_total,
            "rating": float(st["rating"]) if st.get("rating") is not None else None,
            "eta_minutes": int(st["eta_minutes"]) if st.get("eta_minutes") is not None else None,
        })
    evaluated.sort(key=lambda x: (not x["meets_min_order"], x["true_final_cost_toman"]))
    return {
        "best_store": evaluated[0] if evaluated else None,
        "stores_comparison": evaluated,
    }


@mcp.tool(
    name="compare_iranian_book_platforms",
    description="مقایسه قیمت کتاب در طاقچه، فیدیبو، سی‌بوک و دیجی‌کالا به تومان.",
)
async def compare_iranian_book_platforms(
    book_title: str,
    offers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    valid = [
        o for o in offers
        if isinstance(o, dict) and isinstance(o.get("price_toman"), (int, float)) and o["price_toman"] >= 0
    ]
    sorted_offers = sorted(valid, key=lambda x: int(x["price_toman"]))
    return {
        "book_title": normalize_persian_text(book_title),
        "cheapest_option": sorted_offers[0] if sorted_offers else None,
        "all_formats": sorted_offers,
    }


@mcp.tool(
    name="list_supported_iranian_stores",
    description="دریافت لیست فروشگاه‌های ثبت‌شده در رجیستری و لیست ورکرهای زنده MCP به صورت تفکیک‌شده.",
)
async def list_supported_iranian_stores() -> Dict[str, Any]:
    return {
        "total_stores": len(STORE_REGISTRY),
        "stores": STORE_REGISTRY,
        "total_mcp_workers": len(MCP_WORKER_ENDPOINTS),
        "mcp_workers": MCP_WORKER_ENDPOINTS,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
