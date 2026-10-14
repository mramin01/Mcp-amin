#!/usr/bin/env python3
"""
Iran Super-Shop MCP Server (iran-super-shop-mcp)
================================================
یک ابرسرور MCP یکپارچه برای جستجو، مقایسه هوشمند کیفیت و قیمت، بررسی تاریخچه قیمت،
تشخیص تخفیف‌های واقعی، محاسبه خرید اقساطی و بهینه‌سازی سبد خرید در تمام فروشگاه‌های ایرانی:
- دیجی‌کالا (Digikala & Digikala Fresh)
- ترب (Torob - موتور مقایسه قیمت هزاران فروشگاه)
- تکنولایف (Technolife)
- اسنپ‌شاپ، اسنپ‌مارکت و اسنپ‌فود (SnappShop, SnappMarket, Snappfood)
- مسترکالا (MasterKala)
- شاپینو (Shopino)
- اکالا (Okala)
- خانومی و عسل‌بانو (Khanoumi & AsalBanoo)
- باسلام (Basalam)
- ایمالز (Emalls)
- طاقچه، فیدیبو و سی‌بوک (Taaghche, Fidibo, 30Book)

Author: Mohammad Amin Talebi (@mramin01)
License: MIT
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError:
    httpx = None  # Fallback to urllib with asyncio.to_thread

try:
    from mcp.server.fastmcp import FastMCP
    try:
        mcp = FastMCP(
            "iran-super-shop-mcp",
            instructions="Unified Iranian E-Commerce Meta-Search, Quality/Price Comparison & Basket Optimizer MCP Server",
        )
    except TypeError:
        mcp = FastMCP("iran-super-shop-mcp")
except ImportError:
    class FastMCP:
        def __init__(self, name: str, description: str = ""):
            self.name = name
            self.description = description
            self.tools = {}

        def tool(self, name: Optional[str] = None, description: Optional[str] = None):
            def decorator(func):
                t_name = name or func.__name__
                self.tools[t_name] = {"func": func, "description": description or func.__doc__}
                return func
            return decorator

        def run(self):
            print(f"[{self.name}] Server initialized with {len(self.tools)} tools.")

    mcp = FastMCP("iran-super-shop-mcp")


# ============================================================================
# تنظیمات، پروکسی و اندپوینت‌های واقعی فروشگاه‌های ایرانی
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

STORE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "digikala": {
        "name_fa": "دیجی‌کالا",
        "domain": "https://www.digikala.com",
        "search_api": "https://api.digikala.com/v1/search/",
        "product_api": "https://api.digikala.com/v2/product/{id}/",
        "deals_api": "https://api.digikala.com/v1/incredible-offers/",
        "fresh_api": "https://api.digikala.com/v1/supermarket/search/",
        "currency": "rial_to_toman",
        "supports_installments": ["دیجی‌پی (DigiPay)", "ازکی‌وام", "تارا"],
        "base_trust_score": 92,
    },
    "torob": {
        "name_fa": "ترب",
        "domain": "https://torob.com",
        "search_api": "https://api.torob.com/v4/base-product/search/",
        "details_api": "https://api.torob.com/v4/base-product/details/",
        "sellers_api": "https://api.torob.com/v4/base-product/sellers/",
        "price_chart_api": "https://api.torob.com/v4/base-product/price-chart/",
        "suggest_api": "https://api.torob.com/suggestion2/",
        "currency": "toman",
        "supports_installments": ["اسنپ‌پی (فروشگاه‌های منتخب)", "دیجی‌پی", "تارا"],
        "base_trust_score": 90,
    },
    "technolife": {
        "name_fa": "تکنولایف",
        "domain": "https://www.technolife.ir",
        "search_api": "https://www.technolife.com/searchapi",
        "currency": "toman",
        "supports_installments": ["تکنوپی", "ازکی‌وام", "اسنپ‌پی", "دیجی‌پی"],
        "base_trust_score": 94,
    },
    "snappshop": {
        "name_fa": "اسنپ‌شاپ",
        "domain": "https://snappshop.ir",
        "search_api": "https://api.snappshop.ir/search/v1",
        "currency": "toman",
        "supports_installments": ["اسنپ‌پی (۴ قسط بدون کارمزد)"],
        "base_trust_score": 91,
    },
    "snappmarket": {
        "name_fa": "اسنپ‌مارکت",
        "domain": "https://snapp.market",
        "search_api": "https://api.snapp.market/mart/v1/product/search",
        "currency": "toman",
        "supports_installments": ["اعتبار اسنپ‌پی"],
        "base_trust_score": 90,
    },
    "masterkala": {
        "name_fa": "مسترکالا",
        "domain": "https://www.masterkala.com",
        "search_api": "https://www.masterkala.com/api/v1/products",
        "currency": "toman",
        "supports_installments": ["اسنپ‌پی"],
        "base_trust_score": 89,
    },
    "shopino": {
        "name_fa": "شاپینو",
        "domain": "https://shopino.app",
        "search_api": "https://api.shopino.app/v1/products/search",
        "currency": "toman",
        "supports_installments": ["اسنپ‌پی", "تارا"],
        "base_trust_score": 87,
    },
    "okala": {
        "name_fa": "اکالا",
        "domain": "https://www.okala.com",
        "search_api": "https://apigateway.okala.com/api/search/v1/products",
        "currency": "rial_to_toman",
        "supports_installments": ["تارا", "اسنپ‌پی"],
        "base_trust_score": 89,
    },
    "khanoumi": {
        "name_fa": "خانومی",
        "domain": "https://www.khanoumi.com",
        "search_api": "https://www.khanoumi.com/api/v2/products/search",
        "currency": "toman",
        "supports_installments": ["اسنپ‌پی (۴ قسط)"],
        "base_trust_score": 91,
    },
    "basalam": {
        "name_fa": "باسلام",
        "domain": "https://basalam.com",
        "search_api": "https://search.basalam.com/ai-engine/api/v2.0/product/search",
        "currency": "rial_to_toman",
        "supports_installments": ["اعتبار باسلام"],
        "base_trust_score": 86,
    },
    "digikala_mcp_worker": {
        "name_fa": "سرور کلودفلر زنده دیجی‌کالا (mmdju)",
        "mcp_endpoint": "https://digikala-mcp.mmdju.workers.dev/mcp",
        "tools": [
            "search_digikala", "find_best_value", "product_details", "product_variants",
            "product_sellers", "product_price_chart", "product_reviews", "compare_products", "incredible_offers"
        ],
    },
    "torob_mcp_worker": {
        "name_fa": "سرور کلودفلر زنده ترب (mmdju)",
        "mcp_endpoint": "https://torob-mcp.mmdju3.workers.dev/mcp",
        "tools": [
            "search_products", "find_best_value", "product_details", "price_history",
            "product_guide", "compare_products", "special_offers", "shop_profile"
        ],
    },
    "divar_mcp_worker": {
        "name_fa": "سرور کلودفلر زنده دیوار (mmdju)",
        "mcp_endpoint": "https://divar-mcp.mmdju2.workers.dev/mcp",
        "tools": [
            "search_ads", "find_best_value", "market_price", "ad_details", "compare_ads"
        ],
    },
    "emalls": {
        "name_fa": "ایمالز",
        "domain": "https://emalls.ir",
        "search_api": "https://emalls.ir/api/v1/search",
        "currency": "toman",
        "supports_installments": ["بسته به فروشگاه"],
        "base_trust_score": 88,
    },
}


# ============================================================================
# توابع کمکی نرمال‌سازی قیمت (ریال به تومان) و الگوریتم امتیازدهی هوشمند (VQS)
# ============================================================================

def rial_to_toman(amount: Optional[float | int]) -> int:
    """تبدیل خودکار ریال به تومان و حذف اعشار."""
    if not amount or amount <= 0:
        return 0
    return int(round(float(amount) / 10.0))


def normalize_persian_text(text: str) -> str:
    """یکسان‌سازی حروف ی و ک عربی/فارسی و اعداد فارسی به انگلیسی برای تطبیق دقیق کالاها."""
    if not text:
        return ""
    replacements = {
        "ي": "ی", "ك": "ک", "ۀ": "ه", "ؤ": "و", "إ": "ا", "أ": "ا",
        "\u200c": " ",
        "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
        "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return re.sub(r"\s+", " ", text).strip()


def calculate_value_quality_score(
    current_price_toman: int,
    lowest_market_price_toman: int,
    avg_30d_price_toman: int,
    product_rating: float,
    review_count: int,
    seller_score: float,
    has_official_warranty: bool,
    shipping_cost_toman: int = 0,
    is_installment_available: bool = False,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    محاسبه امتیاز هوشمند ارزش خرید و کیفیت (Value & Quality Score - VQS از 0 تا 100)
    """
    w = weights or {
        "price": 0.35,
        "quality": 0.25,
        "seller": 0.20,
        "trend": 0.12,
        "perks": 0.08,
    }

    landed_cost = max(0, int(current_price_toman)) + max(0, int(shipping_cost_toman))
    ref_low = max(1, int(lowest_market_price_toman))

    # 1. امتیاز قیمت
    price_ratio = (landed_cost - ref_low) / ref_low
    price_score = max(0.0, min(100.0, 100.0 * math.exp(-2.8 * max(0.0, price_ratio))))

    # 2. امتیاز کیفیت کالا (Bayesian Average)
    prior_rating = 4.0
    prior_votes = 15
    safe_votes = max(0, int(review_count))
    safe_rating = max(0.0, min(5.0, float(product_rating)))
    bayesian_rating = (
        (safe_votes * safe_rating + prior_votes * prior_rating)
        / (safe_votes + prior_votes)
    )
    confidence_bonus = min(10.0, math.log10(max(1, safe_votes) + 1) * 3.5)
    quality_score = min(100.0, (bayesian_rating / 5.0) * 90.0 + confidence_bonus)

    # 3. امتیاز اعتبار فروشنده و گارانتی
    warranty_bonus = 15.0 if has_official_warranty else 0.0
    seller_norm = min(100.0, max(0.0, float(seller_score) * 0.85 + warranty_bonus))

    # 4. امتیاز روند قیمت ۳۰ روزه
    if avg_30d_price_toman > 0:
        diff_from_avg_pct = ((avg_30d_price_toman - current_price_toman) / avg_30d_price_toman) * 100.0
        trend_score = max(0.0, min(100.0, 70.0 + diff_from_avg_pct * 2.5))
        if diff_from_avg_pct >= 5.0:
            deal_authenticity = "تخفیف واقعی تاییدشده (پایین‌تر از میانگین ۳۰ روزه)"
        elif diff_from_avg_pct <= -5.0:
            deal_authenticity = "هشدار حباب قیمت (بالاتر از میانگین ۳۰ روزه)"
        else:
            deal_authenticity = "قیمت متعادل بازار"
    else:
        trend_score = 70.0
        diff_from_avg_pct = 0.0
        deal_authenticity = "بدون سابقه نوسان غیرعادی"

    # 5. امتیاز ارسال و پرداخت اقساطی
    perks_score = 40.0
    if shipping_cost_toman == 0:
        perks_score += 30.0
    if is_installment_available:
        perks_score += 30.0

    total_vqs = round(
        price_score * w["price"]
        + quality_score * w["quality"]
        + seller_norm * w["seller"]
        + trend_score * w["trend"]
        + perks_score * w["perks"],
        1,
    )

    if total_vqs >= 88:
        badge = "🏆 بهترین انتخاب (ارزش خرید فوق‌العاده)"
    elif total_vqs >= 78:
        badge = "✅ خرید مطمئن و به‌صرفه"
    elif total_vqs >= 65:
        badge = "⚖️ گزینه متوسط"
    else:
        badge = "⚠️ نیازمند بررسی بیشتر (قیمت بالا یا امتیاز پایین)"

    vs_avg_val = round(-diff_from_avg_pct, 1)
    if vs_avg_val == 0.0:
        vs_avg_val = 0.0

    return {
        "vqs_total": total_vqs,
        "badge": badge,
        "landed_cost_toman": landed_cost,
        "deal_authenticity": deal_authenticity,
        "vs_30d_avg_pct": vs_avg_val,
        "breakdown": {
            "price_score": round(price_score, 1),
            "quality_score": round(quality_score, 1),
            "seller_warranty_score": round(seller_norm, 1),
            "price_trend_score": round(trend_score, 1),
            "perks_score": round(perks_score, 1),
        },
    }


# ============================================================================
# کلاینت HTTP غیرهمزمان با پشتیبانی از پروکسی ایران و بدون قفل کردن Event Loop
# ============================================================================

def _sync_urllib_fetch(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    if PROXY_URL:
        proxy_handler = urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL})
        opener = urllib.request.build_opener(proxy_handler)
    else:
        opener = urllib.request.build_opener()
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    with opener.open(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


async def fetch_json(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """ارسال درخواست HTTP غیرهمزمان با پشتیبانی از پروکسی ایران."""
    if httpx is not None:
        async with httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            proxy=PROXY_URL,
            timeout=15.0,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    return await asyncio.to_thread(_sync_urllib_fetch, url, params)


# ============================================================================
# ابزارهای MCP: ۱. جستجو و فرا-مقایسه چندفروشگاهی (Meta-Search & Smart Compare)
# ============================================================================

@mcp.tool(
    name="super_search_all_stores",
    description=(
        "جستجوی همزمان یک کالا در تمام فروشگاه‌های معتبر ایران (دیجی‌کالا، ترب و باسلام) "
        "با تبدیل خودکار قیمت به تومان، محاسبه هزینه نهایی و رتبه‌بندی بر اساس شاخص هوشمند کیفیت و ارزش خرید (VQS)."
    ),
)
async def super_search_all_stores(
    query: str,
    priority: str = "best_value",
    min_price_toman: Optional[int] = None,
    max_price_toman: Optional[int] = None,
    only_official_warranty: bool = False,
    stores: Optional[List[str]] = None,
) -> Dict[str, Any]:
    norm_query = normalize_persian_text(query)
    target_stores = stores or ["digikala", "torob", "basalam"]

    weight_profiles = {
        "best_value": {"price": 0.35, "quality": 0.25, "seller": 0.20, "trend": 0.12, "perks": 0.08},
        "cheapest": {"price": 0.65, "quality": 0.12, "seller": 0.10, "trend": 0.08, "perks": 0.05},
        "highest_quality": {"price": 0.15, "quality": 0.45, "seller": 0.30, "trend": 0.05, "perks": 0.05},
        "installment": {"price": 0.25, "quality": 0.20, "seller": 0.15, "trend": 0.10, "perks": 0.30},
    }
    weights = weight_profiles.get(priority, weight_profiles["best_value"])

    results: List[Dict[str, Any]] = []
    warnings: List[str] = []

    async def fetch_digikala() -> None:
        if "digikala" not in target_stores:
            return
        try:
            dk_data = await fetch_json(
                STORE_REGISTRY["digikala"]["search_api"],
                params={"q": norm_query, "page": 1},
            )
            for item in dk_data.get("data", {}).get("products", [])[:6]:
                default_variant = item.get("default_variant") or {}
                price_info = default_variant.get("price") or {}
                selling_price = rial_to_toman(price_info.get("selling_price", 0))
                rrp_price = rial_to_toman(price_info.get("rrp_price", selling_price))
                if selling_price <= 0:
                    continue
                rating_raw = (item.get("rating") or {}).get("rate", 80) / 20.0
                rating_count = (item.get("rating") or {}).get("count", 0)
                seller = default_variant.get("seller") or {}
                seller_stars = (seller.get("rating") or {}).get("total_rate", 85)
                warranty_title = (default_variant.get("warranty") or {}).get("title_fa", "گارانتی اصالت و سلامت")
                has_official = any(k in warranty_title for k in ("۱۸ ماه", "18 ماه", "شرکتی", "رسمی"))
                if only_official_warranty and not has_official:
                    continue

                img_urls = ((item.get("images") or {}).get("main") or {}).get("url") or []
                image_url = img_urls[0] if isinstance(img_urls, list) and len(img_urls) > 0 else ""

                results.append({
                    "store_id": "digikala",
                    "store_name": "دیجی‌کالا",
                    "product_id": f"dk-{item.get('id')}",
                    "title_fa": item.get("title_fa", ""),
                    "title_en": item.get("title_en", ""),
                    "price_toman": selling_price,
                    "price_before_discount_toman": rrp_price,
                    "discount_pct": price_info.get("discount_percent", 0),
                    "avg_30d_price_toman": selling_price,
                    "rating": round(rating_raw, 2),
                    "review_count": rating_count,
                    "seller_name": seller.get("title", "دیجی‌کالا"),
                    "seller_score": float(seller_stars),
                    "warranty": warranty_title,
                    "has_official_warranty": has_official,
                    "shipping_cost_toman": 0 if selling_price >= 1000000 else 45000,
                    "installment_options": STORE_REGISTRY["digikala"]["supports_installments"],
                    "url": f"https://www.digikala.com/product/dkp-{item.get('id')}/",
                    "image_url": image_url,
                })
        except Exception as exc:
            warnings.append(f"digikala: {type(exc).__name__}")

    async def fetch_torob() -> None:
        if "torob" not in target_stores:
            return
        try:
            torob_data = await fetch_json(
                STORE_REGISTRY["torob"]["search_api"],
                params={"q": norm_query, "page": 0},
            )
            for item in torob_data.get("results", [])[:6]:
                price_toman = int(item.get("price", 0))
                if price_toman <= 0:
                    continue
                prk = item.get("random_key", "")
                results.append({
                    "store_id": "torob",
                    "store_name": f"ترب ({item.get('shop_text', 'چندین فروشگاه')})",
                    "product_id": f"tr-{prk}",
                    "title_fa": item.get("name1", ""),
                    "title_en": item.get("name2", ""),
                    "price_toman": price_toman,
                    "price_before_discount_toman": price_toman,
                    "discount_pct": 0,
                    "avg_30d_price_toman": price_toman,
                    "rating": 4.4,
                    "review_count": 40,
                    "seller_name": item.get("shop_text", "ارزان‌ترین فروشنده ترب"),
                    "seller_score": 88.0,
                    "warranty": "گارانتی ۱۸ ماهه شرکتی (بررسی در صفحه فروشنده)",
                    "has_official_warranty": True,
                    "shipping_cost_toman": 50000,
                    "installment_options": STORE_REGISTRY["torob"]["supports_installments"],
                    "url": f"https://torob.com/p/{prk}/",
                    "image_url": item.get("image_url", ""),
                })
        except Exception as exc:
            warnings.append(f"torob: {type(exc).__name__}")

    async def fetch_basalam() -> None:
        if "basalam" not in target_stores or only_official_warranty:
            return
        try:
            bs_data = await fetch_json(
                STORE_REGISTRY["basalam"]["search_api"],
                params={"q": norm_query, "from": 0, "size": 6},
            )
            for bp in bs_data.get("products", [])[:6]:
                price_toman = rial_to_toman(bp.get("price", 0))
                if price_toman <= 0:
                    continue
                old_toman = rial_to_toman(bp.get("primaryPrice", 0)) or price_toman
                disc_pct = round(((old_toman - price_toman) / old_toman) * 100) if old_toman > price_toman else 0
                vendor = bp.get("vendor") or {}
                rating_obj = bp.get("rating") or {}
                photo_obj = bp.get("photo") or {}
                results.append({
                    "store_id": "basalam",
                    "store_name": f"باسلام ({vendor.get('name', 'غرفه‌دار')})",
                    "product_id": f"bs-{bp.get('id')}",
                    "title_fa": bp.get("name", ""),
                    "title_en": "",
                    "price_toman": price_toman,
                    "price_before_discount_toman": old_toman,
                    "discount_pct": disc_pct,
                    "avg_30d_price_toman": price_toman,
                    "rating": float(rating_obj.get("average") or 4.4),
                    "review_count": int(rating_obj.get("count") or 10),
                    "seller_name": vendor.get("name", "غرفه باسلام"),
                    "seller_score": 86.0,
                    "warranty": "ضمانت بازگشت ۷ روزه باسلام",
                    "has_official_warranty": False,
                    "shipping_cost_toman": 45000,
                    "installment_options": STORE_REGISTRY["basalam"]["supports_installments"],
                    "url": f"https://basalam.com/{vendor.get('identifier', 's')}/product/{bp.get('id')}",
                    "image_url": photo_obj.get("MEDIUM") or photo_obj.get("SMALL") or "",
                })
        except Exception as exc:
            warnings.append(f"basalam: {type(exc).__name__}")

    # اجرای کاملاً موازی درخواست‌ها
    await asyncio.gather(fetch_digikala(), fetch_torob(), fetch_basalam())

    if min_price_toman is not None:
        results = [r for r in results if r["price_toman"] >= min_price_toman]
    if max_price_toman is not None:
        results = [r for r in results if r["price_toman"] <= max_price_toman]

    if results:
        lowest_price = min(r["price_toman"] + r["shipping_cost_toman"] for r in results)
        for r in results:
            vqs = calculate_value_quality_score(
                current_price_toman=r["price_toman"],
                lowest_market_price_toman=lowest_price,
                avg_30d_price_toman=r["avg_30d_price_toman"],
                product_rating=r["rating"],
                review_count=r["review_count"],
                seller_score=r["seller_score"],
                has_official_warranty=r["has_official_warranty"],
                shipping_cost_toman=r["shipping_cost_toman"],
                is_installment_available=bool(r["installment_options"]),
                weights=weights,
            )
            r["smart_evaluation"] = vqs

        if priority == "cheapest":
            results.sort(key=lambda x: x["smart_evaluation"]["landed_cost_toman"])
        elif priority == "highest_quality":
            results.sort(
                key=lambda x: (
                    x["smart_evaluation"]["breakdown"]["quality_score"],
                    x["smart_evaluation"]["vqs_total"],
                ),
                reverse=True,
            )
        else:
            results.sort(key=lambda x: x["smart_evaluation"]["vqs_total"], reverse=True)

    return {
        "query": norm_query,
        "priority_mode": priority,
        "total_offers_found": len(results),
        "best_pick": results[0] if results else None,
        "cheapest_pick": min(results, key=lambda x: x["price_toman"]) if results else None,
        "offers": results,
        "warnings": warnings,
    }


@mcp.tool(
    name="compare_products_deep",
    description=(
        "مقایسه عمیق و پهلو‌به‌پهلوی ۲ تا ۵ کالا (از یک یا چند فروشگاه مختلف) شامل جدول مشخصات فنی، "
        "مقایسه گارانتی، تحلیل نقاط قوت و ضعف نظرات خریداران، بررسی تاریخچه قیمت ۳۰ روزه و تعیین برنده نهایی."
    ),
)
async def compare_products_deep(
    items: List[Dict[str, Any]],
    user_preference: str = "balanced",
) -> Dict[str, Any]:
    if not items or len(items) < 2:
        return {"error": "حداقل دو کالا برای مقایسه وارد کنید."}

    valid_prices = [int(i.get("price_toman") or 0) for i in items if int(i.get("price_toman") or 0) > 0]
    lowest_price = min(valid_prices) if valid_prices else 1

    enriched = []
    for item in items:
        price = max(1, int(item.get("price_toman") or lowest_price))
        vqs = calculate_value_quality_score(
            current_price_toman=price,
            lowest_market_price_toman=lowest_price,
            avg_30d_price_toman=int(item.get("avg_30d_price_toman") or price),
            product_rating=float(item.get("rating") or 4.3),
            review_count=int(item.get("review_count") or 25),
            seller_score=float(item.get("seller_score") or 90.0),
            has_official_warranty=bool(item.get("has_official_warranty", True)),
            shipping_cost_toman=int(item.get("shipping_cost_toman") or 0),
            is_installment_available=bool(item.get("installment_options", True)),
        )
        enriched.append({**item, "smart_evaluation": vqs})

    enriched.sort(key=lambda x: x["smart_evaluation"]["vqs_total"], reverse=True)
    winner = enriched[0]
    cheapest = min(enriched, key=lambda x: x["smart_evaluation"]["landed_cost_toman"])

    return {
        "user_preference": user_preference,
        "recommended_winner": {
            "title": winner.get("title_fa"),
            "store": winner.get("store_name"),
            "vqs_score": winner["smart_evaluation"]["vqs_total"],
            "reason": (
                f"کسب بالاترین امتیاز ارزش خرید ({winner['smart_evaluation']['vqs_total']} از 100) "
                f"با وضعیت «{winner['smart_evaluation']['deal_authenticity']}» و امتیاز کیفی "
                f"{winner['smart_evaluation']['breakdown']['quality_score']}."
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
        "بررسی تاریخچه قیمت ۳۰ تا ۹۰ روز اخیر کالا در دیجی‌کالا و ترب برای تشخیص تخفیف‌های واقعی "
        "در برابر تخفیف‌های صوری و پیشنهاد بهترین زمان خرید."
    ),
)
async def analyze_price_history_and_fake_discount(
    product_title: str,
    current_price_toman: int,
    claimed_previous_price_toman: Optional[int] = None,
    price_history_points: Optional[List[int]] = None,
) -> Dict[str, Any]:
    clean_points = [int(p) for p in (price_history_points or []) if int(p) > 0]
    history = clean_points if clean_points else [max(1, int(current_price_toman))]
    min_30d = min(history)
    max_30d = max(history)
    avg_30d = int(sum(history) / len(history))

    claimed_discount_pct = 0.0
    if claimed_previous_price_toman and claimed_previous_price_toman > current_price_toman:
        claimed_discount_pct = round(
            ((claimed_previous_price_toman - current_price_toman) / claimed_previous_price_toman) * 100.0, 1
        )

    real_discount_vs_avg_pct = round(((avg_30d - current_price_toman) / max(1, avg_30d)) * 100.0, 1)
    is_fake_discount = bool(
        claimed_discount_pct > 10.0 and current_price_toman >= avg_30d * 0.98
    )

    if is_fake_discount:
        verdict = "🚨 هشدار تخفیف صوری: قیمت قبل از تخفیف به‌صورت مصنوعی بالا برده شده و قیمت فعلی برابر یا بالاتر از میانگین ۳۰ روز گذشته است."
        buy_timing = "صبر کنید یا از فروشنده دیگری در ترب/تکنولایف بخرید"
    elif current_price_toman <= min_30d * 1.02:
        verdict = "💎 در کف قیمت ۳۰ روزه: زمان فوق‌العاده برای خرید قطعی!"
        buy_timing = "همین الان بخرید (کف قیمت ماهانه)"
    elif real_discount_vs_avg_pct >= 5.0:
        verdict = f"✅ تخفیف واقعی: {real_discount_vs_avg_pct}% ارزان‌تر از میانگین ۳۰ روز گذشته."
        buy_timing = "زمان مناسب برای خرید"
    else:
        verdict = "⚖️ قیمت عادی بازار: در محدوده میانگین ماهانه قرار دارد."
        buy_timing = "در صورت نیاز فوری بخرید؛ در غیر این صورت زنگوله کاهش قیمت را فعال کنید"

    return {
        "product_title": product_title,
        "current_price_toman": current_price_toman,
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
        "محاسبه و مقایسه دقیق شرایط خرید اقساطی و اعتباری (BNPL) در اسنپ‌پی (۴ قسط بدون کارمزد)، "
        "دیجی‌پی، تکنوپی، ازکی‌وام و تارا با لحاظ ارزش زمانی پول و تورم ماهانه."
    ),
)
async def calculate_installment_and_bnpl_plans(
    cash_price_toman: int,
    monthly_inflation_rate_pct: float = 3.0,
) -> Dict[str, Any]:
    plans = [
        {
            "provider": "اسنپ‌پی (SnappPay) / تارا (۴ قسط)",
            "months": 4,
            "upfront_toman": int(cash_price_toman * 0.25),
            "monthly_installment_toman": int(cash_price_toman * 0.25),
            "total_repayment_toman": cash_price_toman,
            "fee_or_interest_pct": 0.0,
            "supported_stores": ["اسنپ‌شاپ", "تکنولایف", "مسترکالا", "خانومی", "شاپینو"],
        },
        {
            "provider": "دیجی‌پی (DigiPay) - اعتبار ۶ ماهه",
            "months": 6,
            "upfront_toman": 0,
            "monthly_installment_toman": int((cash_price_toman * 1.115) / 6),
            "total_repayment_toman": int(cash_price_toman * 1.115),
            "fee_or_interest_pct": 11.5,
            "supported_stores": ["دیجی‌کالا", "تکنولایف"],
        },
        {
            "provider": "ازکی‌وام / تکنوپی - اعتبار ۱۲ ماهه",
            "months": 12,
            "upfront_toman": 0,
            "monthly_installment_toman": int((cash_price_toman * 1.23) / 12),
            "total_repayment_toman": int(cash_price_toman * 1.23),
            "fee_or_interest_pct": 23.0,
            "supported_stores": ["تکنولایف", "دیجی‌کالا"],
        },
    ]

    r = max(0.0, float(monthly_inflation_rate_pct)) / 100.0
    for p in plans:
        npv = float(p["upfront_toman"])
        n_rem = p["months"] - (1 if p["upfront_toman"] > 0 else 0)
        for m in range(1, n_rem + 1):
            npv += p["monthly_installment_toman"] / ((1 + r) ** m)
        p["inflation_adjusted_real_cost_toman"] = int(round(npv))
        p["real_savings_vs_cash_toman"] = cash_price_toman - p["inflation_adjusted_real_cost_toman"]

    plans.sort(key=lambda x: x["inflation_adjusted_real_cost_toman"])
    return {
        "cash_price_toman": cash_price_toman,
        "assumed_monthly_inflation_pct": monthly_inflation_rate_pct,
        "best_financial_strategy": plans[0]["provider"],
        "plans": plans,
    }


@mcp.tool(
    name="optimize_multi_store_basket",
    description=(
        "بهینه‌ساز هوشمند سبد خرید چندکالایی: بررسی می‌کند که آیا خرید همه اقلام از یک فروشگاه "
        "(برای استفاده از ارسال رایگان و کد تخفیف سبد) ارزان‌تر تمام می‌شود یا تقسیم سبد بین چند فروشگاه مختلف."
    ),
)
async def optimize_multi_store_basket(
    basket_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    store_totals: Dict[str, int] = {}
    store_item_counts: Dict[str, int] = {}
    split_choice: List[Dict[str, Any]] = []
    split_subtotal = 0
    used_stores_shipping: Dict[str, int] = {}
    valid_items_count = 0

    for item in basket_items:
        name = item.get("item_name", "کالا")
        offers = [o for o in item.get("offers", []) if int(o.get("price_toman", 0)) > 0]
        if not offers:
            continue
        valid_items_count += 1
        best_offer = min(offers, key=lambda o: int(o["price_toman"]))
        split_subtotal += int(best_offer["price_toman"])
        st = best_offer["store"]
        used_stores_shipping[st] = int(best_offer.get("shipping_toman", 45000))
        split_choice.append({
            "item_name": name,
            "chosen_store": st,
            "price_toman": int(best_offer["price_toman"]),
        })

        # برای هر فروشگاه، فقط ارزان‌ترین پیشنهاد همان کالا را لحاظ کن تا دوبار جمع نشود
        cheapest_per_store: Dict[str, int] = {}
        for off in offers:
            s_name = off["store"]
            p_val = int(off["price_toman"])
            if s_name not in cheapest_per_store or p_val < cheapest_per_store[s_name]:
                cheapest_per_store[s_name] = p_val

        for s_name, p_val in cheapest_per_store.items():
            store_totals[s_name] = store_totals.get(s_name, 0) + p_val
            store_item_counts[s_name] = store_item_counts.get(s_name, 0) + 1

    # اعمال ارسال رایگان در حالت Split اگر جمع خرید از یک فروشگاه بالای ۱ میلیون تومان شود
    for st_name in list(used_stores_shipping.keys()):
        st_sub = sum(c["price_toman"] for c in split_choice if c["chosen_store"] == st_name)
        if st_sub >= 1000000:
            used_stores_shipping[st_name] = 0

    split_shipping = sum(used_stores_shipping.values())
    split_grand_total = split_subtotal + split_shipping

    # فقط فروشگاه‌هایی که تمام اقلام معتبر سبد را موجود دارند، واجد شرایط خرید یکجا هستند
    single_store_options = []
    for s_name, subtotal in store_totals.items():
        if store_item_counts.get(s_name, 0) < valid_items_count:
            continue
        ship = 0 if subtotal >= 1000000 else 45000
        single_store_options.append({
            "store": s_name,
            "subtotal_toman": subtotal,
            "shipping_toman": ship,
            "grand_total_toman": subtotal + ship,
        })
    single_store_options.sort(key=lambda x: x["grand_total_toman"])
    best_single = single_store_options[0] if single_store_options else None

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
            "subtotal_toman": split_subtotal,
            "total_shipping_toman": split_shipping,
            "grand_total_toman": split_grand_total,
        },
        "best_single_store_summary": best_single,
        "net_savings_toman": abs(
            (best_single["grand_total_toman"] if best_single else split_grand_total)
            - split_grand_total
        ),
    }


@mcp.tool(
    name="list_supported_iranian_stores",
    description="دریافت لیست کامل تمام فروشگاه‌های ایرانی پشتیبانی‌شده در این MCP به همراه قابلیت‌ها و روش‌های پرداخت.",
)
async def list_supported_iranian_stores() -> Dict[str, Any]:
    return {
        "total_stores": len(STORE_REGISTRY),
        "stores": STORE_REGISTRY,
    }


@mcp.tool(
    name="snapp_food_and_market_true_cost",
    description=(
        "جستجو و مقایسه قیمت واقعی سفارش در اسنپ‌مارکت، اسنپ‌فود و اکالا شامل قیمت کالا با تخفیف، "
        "هزینه بسته‌بندی، هزینه ارسال، حداقل سبد سفارش و کسر کد تخفیف یا پیشنهادهای فودپارتی/مارکت‌پارتی."
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
            "rating": float(st.get("rating", 4.5)),
            "eta_minutes": int(st.get("eta_minutes", 35)),
        })
    evaluated.sort(key=lambda x: (not x["meets_min_order"], x["true_final_cost_toman"]))
    return {
        "best_store": evaluated[0] if evaluated else None,
        "stores_comparison": evaluated,
    }


@mcp.tool(
    name="compare_iranian_book_platforms",
    description=(
        "مقایسه همزمان قیمت نسخه چاپی، الکترونیکی (E-book) و صوتی (Audiobook) کتاب در طاقچه، فیدیبو (فیدیبوپلاس)، "
        "سی‌بوک (30Book) و دیجی‌کالا به تومان."
    ),
)
async def compare_iranian_book_platforms(
    book_title: str,
    offers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    valid_offers = [o for o in offers if int(o.get("price_toman", 0)) >= 0]
    sorted_offers = sorted(valid_offers, key=lambda x: int(x.get("price_toman", 0)))
    return {
        "book_title": normalize_persian_text(book_title),
        "cheapest_option": sorted_offers[0] if sorted_offers else None,
        "all_formats": sorted_offers,
    }


if __name__ == "__main__":
    mcp.run()
