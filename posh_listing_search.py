"""
Helpers for searching Poshmark listings through the Poshmark server (goshposh/server).

The server exposes listing search at GET /api/posts (lib/api_app.rb `get '/posts'`).
The search request is sent as the `request` query parameter, JSON encoded, exactly as
the iOS/web clients send it. Request schema:
goshposh/server/docs/post_search/openapi_spec/components/requests.yaml
"""

import json
import logging
import re
from statistics import mean, median
from typing import Optional

import httpx

logger = logging.getLogger("poshbot_mcp.listing_search")

# Poshmark server base URL. The same host also serves posh_bot_stats.
POSH_API_BASE = "https://stage.goshd.com/api"
LISTING_URL = "https://stage.goshd.com/listing/{listing_id}"
HTTP_TIMEOUT_SECONDS = 15
MAX_RESULTS = 20
PRICE_BAND_PERCENT = 5.0  # similar-price band: listing price -5% .. +5%
MAX_QUERY_WORDS = 5  # the search engine does best with a few strong words, not a full title
DEFAULT_SORT = "relevance_v2"  # the same default the Poshmark apps use
SORT_OPTIONS = ("relevance_v2", "best_match", "added_desc", "price_asc", "price_desc")
# The server runs price_asc / price_desc / added_desc through a much stricter matcher
# (2 hits where relevance finds 78 for the same words), so those orders are applied
# here after collecting; the server is always queried with a relevance sort.
LOCAL_SORTS = ("price_asc", "price_desc", "added_desc")
# One request per tool call. The subcategory (category_feature) filter is never sent:
# near-identical listings are filed under different subcategories, so it mostly removed
# the matches we wanted. Brand, department and category are the similarity filters.


def build_search_request(
    query: str,
    brand: Optional[str] = None,
    department_id: Optional[str] = None,
    category_id: Optional[str] = None,
    condition: Optional[str] = None,
    price_range: Optional[tuple] = None,
    currency_code: str = "USD",
    sort_by: str = DEFAULT_SORT,
    count: int = 10,
) -> dict:
    """Builds the JSON body for GET /api/posts. Only available listings are searched."""
    if sort_by not in SORT_OPTIONS:
        sort_by = DEFAULT_SORT
    filters: dict = {"inventory_status": ["available"]}
    if brand:
        filters["brand"] = [brand]
    if department_id:
        filters["department"] = [department_id]
    if category_id:
        filters["category_v2"] = [category_id]
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


def trim_query(query: Optional[str], max_words: int = MAX_QUERY_WORDS) -> str:
    """
    Keeps at most `max_words` words of the query. The model is asked to send only the
    important words (brand + item type + a detail or two); this is the safety net for
    when it pastes a whole title anyway.
    """
    words = re.findall(r"[^\s]+", query or "")
    return " ".join(words[:max_words])


def price_band(listing_price: Optional[float], percent: float = PRICE_BAND_PERCENT) -> Optional[tuple]:
    """
    Turns the listing price the client sent into the (min, max) range to search:
    5% below and 5% above the price. Returns None when no price was given.
    """
    if listing_price is None:
        return None
    price = float(listing_price)
    if price <= 0:
        raise ValueError("listing_price must be a positive number")
    factor = percent / 100.0
    return (round(price * (1.0 - factor), 2), round(price * (1.0 + factor), 2))


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
        "listed_at": post.get("first_published_at") or post.get("created_at"),
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
    logger.info("listing search request: %s", search_request)
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


async def search_listings(
    query: str,
    brand: Optional[str] = None,
    department_id: Optional[str] = None,
    category_id: Optional[str] = None,
    condition: Optional[str] = None,
    price_range: Optional[tuple] = None,
    currency_code: str = "USD",
    sort_by: str = DEFAULT_SORT,
    count: int = 10,
    exclude_listing_id: Optional[str] = None,
) -> dict:
    """
    Makes exactly one search request with the filters given and returns compact
    listings plus a price summary.

    The server is always queried with a relevance sort. price_asc, price_desc and
    added_desc are applied to the returned list afterwards (see LOCAL_SORTS), because
    the server's own price and date sorts use a much stricter matcher.
    """
    target = max(1, min(count, MAX_RESULTS))
    requested_sort = sort_by if sort_by in SORT_OPTIONS else DEFAULT_SORT
    server_sort = DEFAULT_SORT if requested_sort in LOCAL_SORTS else requested_sort
    search_request = build_search_request(
        query=query,
        brand=brand,
        department_id=department_id,
        category_id=category_id,
        condition=condition,
        price_range=price_range,
        currency_code=currency_code,
        sort_by=server_sort,
        count=target,
    )
    result = await find_listings(search_request, exclude_listing_id)
    listings = result["listings"][:target]
    if requested_sort == "price_asc":
        listings.sort(key=lambda l: (l["price"] is None, l["price"] or 0.0))
    elif requested_sort == "price_desc":
        listings.sort(key=lambda l: (l["price"] is None, -(l["price"] or 0.0)))
    elif requested_sort == "added_desc":
        listings.sort(key=lambda l: l.get("listed_at") or "", reverse=True)
    logger.info("listing search returned %d listings (total %s)", len(listings), result.get("total_matching_on_poshmark"))
    return {
        "listings": listings,
        "price_summary": price_summary(listings),
        "total_matching_on_poshmark": result.get("total_matching_on_poshmark"),
        "sort_applied": requested_sort,
        "search_used": result.get("search_used"),
    }
