"""
Helpers for searching Poshmark listings through the Poshmark server (goshposh/server).

The server exposes listing search at GET /api/posts (lib/api_app.rb `get '/posts'`).
The search request is sent as the `request` query parameter, JSON encoded, exactly as
the iOS/web clients send it. Request schema:
goshposh/server/docs/post_search/openapi_spec/components/requests.yaml
"""

import json
from statistics import mean, median
from typing import Optional

import httpx

# Poshmark server base URL. The same host also serves posh_bot_stats.
POSH_API_BASE = "https://stage.goshd.com/api"
LISTING_URL = "https://poshmark.com/listing/{listing_id}"
HTTP_TIMEOUT_SECONDS = 15
MAX_RESULTS = 20
PRICE_CEILING = 1_000_000.0  # used when only min_price is given


def build_search_request(
    query: str,
    brand: Optional[str] = None,
    department_id: Optional[str] = None,
    category_id: Optional[str] = None,
    category_feature_id: Optional[str] = None,
    condition: Optional[str] = None,
    price_range: Optional[tuple] = None,
    currency_code: str = "USD",
    sort_by: str = "best_match",
    count: int = 10,
) -> dict:
    """Builds the JSON body for GET /api/posts. Only available listings are searched."""
    filters: dict = {"inventory_status": ["available"]}
    if brand:
        filters["brand"] = [brand]
    if department_id:
        filters["department"] = [department_id]
    if category_id:
        filters["category_v2"] = [category_id]
    if category_feature_id:
        filters["category_feature"] = [category_feature_id]
    if condition:
        filters["condition"] = [condition]
    if price_range:
        low, high = price_range
        filters["price_amount_range"] = [{
            "min": {"val": f"{low:.2f}", "currency_code": currency_code},
            "max": {"val": f"{high:.2f}", "currency_code": currency_code},
        }]
    return {
        "query": query or "",
        "filters": filters,
        "sort_by": sort_by,
        "count": str(max(1, min(count, MAX_RESULTS))),
    }


def normalize_price_range(min_price: Optional[float], max_price: Optional[float]) -> Optional[tuple]:
    """Turns optional min/max into a (low, high) pair, or None when neither is given."""
    if min_price is None and max_price is None:
        return None
    low = max(0.0, float(min_price)) if min_price is not None else 0.0
    high = float(max_price) if max_price is not None else PRICE_CEILING
    if high < low:
        low, high = high, low
    return (low, high)


async def search_posts(search_request: dict) -> dict:
    """Calls GET /api/posts on the Poshmark server and returns the parsed JSON body."""
    params = {
        "request": json.dumps(search_request),
        "app_type": "web",
        "domain": "us",
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = await client.get(f"{POSH_API_BASE}/posts", params=params)
        if response.status_code != 200:
            raise RuntimeError(
                f"Poshmark search returned HTTP {response.status_code}: {response.text[:200]}"
            )
        return response.json()


def money(amount: Optional[dict]) -> Optional[float]:
    """Reads a Poshmark money object ({"val": "12.0", "currency_code": "USD"}) as a float."""
    try:
        return float(amount["val"]) if amount and amount.get("val") is not None else None
    except (TypeError, ValueError):
        return None


def compact_listing(post: dict) -> dict:
    """Keeps only the fields a shopper cares about so the model is not flooded with JSON."""
    catalog = post.get("catalog") or {}
    features = catalog.get("category_feature_objs") or []
    price_amount = post.get("price_amount") or {}
    return {
        "id": post.get("id"),
        "title": post.get("title"),
        "brand": post.get("brand"),
        "price": money(price_amount),
        "original_price": money(post.get("original_price_amount")),
        "currency": price_amount.get("currency_code"),
        "condition": post.get("condition"),
        "size": post.get("size"),
        "department": (catalog.get("department_obj") or {}).get("display"),
        "category": (catalog.get("category_obj") or {}).get("display"),
        "subcategory": features[0].get("display") if features else None,
        "seller_username": post.get("creator_username"),
        "inventory_status": (post.get("inventory") or {}).get("status"),
        "url": LISTING_URL.format(listing_id=post.get("id")),
        "picture_url": post.get("picture_url"),
    }


def price_summary(listings: list) -> dict:
    prices = [l["price"] for l in listings if l.get("price") is not None]
    if not prices:
        return {"count": 0}
    return {
        "count": len(prices),
        "min": min(prices),
        "max": max(prices),
        "average": round(mean(prices), 2),
        "median": round(median(prices), 2),
    }


async def find_listings(search_request: dict, exclude_listing_id: Optional[str] = None) -> dict:
    """Runs the search and returns compact listings plus a price summary."""
    body = await search_posts(search_request)
    posts = body.get("data") or []
    listings = [
        compact_listing(p) for p in posts
        if not exclude_listing_id or p.get("id") != exclude_listing_id
    ]
    return {
        "listings": listings,
        "price_summary": price_summary(listings),
        "total_matching_on_poshmark": body.get("total_listing_count"),
        "search_used": search_request,
    }
