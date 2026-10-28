import json
from statistics import mean, median
from typing import Optional

from fastmcp import FastMCP
import httpx

mcp = FastMCP("PoshBot MCP")

# Poshmark server (goshposh/server) base URL. The same host already serves posh_bot_stats.
POSH_API_BASE = "https://stage.goshd.com/api"
LISTING_URL = "https://poshmark.com/listing/{listing_id}"
HTTP_TIMEOUT_SECONDS = 15
MAX_RESULTS = 20
PRICE_CEILING = 1_000_000.0  # used when only min_price is given


@mcp.tool
async def get_seller_data(seller_id: str):
    """
    Retrieves seller statistics and bot data from an external API.

    This function fetches comprehensive seller information including their
    performance metrics, bot statistics, and activity data from the posh_bot_stats
    endpoint.

    Args:
        seller_id (str): The unique identifier of the seller whose data should be retrieved.
                        This ID is used to construct the API endpoint URL.

    Returns:
        dict: On success, returns the API response containing seller statistics.
              On failure, returns a dictionary with an error message:
              {"msg": "Seller data is not found"}
    """
    api = f"{POSH_API_BASE}/posh_bot/{seller_id}/posh_bot_stats"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            response = await client.get(api)
            if response.status_code == 200:
                return response.json()
            return {"msg": "Seller data is not found"}
    except Exception as e:
        return {"msg": str(e)}


# ---------------------------------------------------------------------------
# Similar listings: backed by the Poshmark server's listing search, GET /api/posts
# (goshposh/server lib/api_app.rb `get '/posts'`). The search request is sent as the
# `request` query parameter, JSON encoded, exactly as the iOS/web clients send it.
# Schema: goshposh/server/docs/post_search/openapi_spec/components/requests.yaml
# ---------------------------------------------------------------------------

def _build_search_request(
    query: str,
    brand: Optional[str],
    department_id: Optional[str],
    category_id: Optional[str],
    category_feature_id: Optional[str],
    condition: Optional[str],
    price_range: Optional[tuple],
    currency_code: str,
    sort_by: str,
    count: int,
) -> dict:
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


async def _search_posts(search_request: dict) -> dict:
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


def _money(amount: Optional[dict]) -> Optional[float]:
    try:
        return float(amount["val"]) if amount and amount.get("val") is not None else None
    except (TypeError, ValueError):
        return None


def _compact_listing(post: dict) -> dict:
    """Keeps only the fields a shopper cares about so the model is not flooded with JSON."""
    catalog = post.get("catalog") or {}
    features = catalog.get("category_feature_objs") or []
    price_amount = post.get("price_amount") or {}
    return {
        "id": post.get("id"),
        "title": post.get("title"),
        "brand": post.get("brand"),
        "price": _money(price_amount),
        "original_price": _money(post.get("original_price_amount")),
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


def _price_summary(listings: list) -> dict:
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


async def _find_listings(search_request: dict, exclude_listing_id: Optional[str]) -> dict:
    body = await _search_posts(search_request)
    posts = body.get("data") or []
    listings = [
        _compact_listing(p) for p in posts
        if not exclude_listing_id or p.get("id") != exclude_listing_id
    ]
    return {
        "listings": listings,
        "price_summary": _price_summary(listings),
        "total_matching_on_poshmark": body.get("total_listing_count"),
        "search_used": search_request,
    }


@mcp.tool
async def find_similar_listings(
    query: str,
    brand: Optional[str] = None,
    department_id: Optional[str] = None,
    category_id: Optional[str] = None,
    category_feature_id: Optional[str] = None,
    condition: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    currency_code: str = "USD",
    exclude_listing_id: Optional[str] = None,
    count: int = 10,
    sort_by: str = "best_match",
):
    """
    Finds other Poshmark listings similar to the one the buyer is looking at,
    optionally limited to a price range.

    Use this when the buyer asks things like "are there similar items?", "show me
    other options like this", "what else is available in this style?", and also for
    price questions such as "is this a good price?", "is this overpriced?",
    "anything like this but cheaper?". For price questions pass min_price and
    max_price (for example 75% and 125% of the listing price, or 0 to the listing
    price for cheaper alternatives) and sort_by="price_asc". The response includes a
    price_summary you can compare the current listing against. This compares ONLY
    against other Poshmark listings, never other websites. Only listings currently
    available on Poshmark are returned.

    Take the arguments from the current listing's data:
      - query: the listing title (or brand + item type, e.g. "Ganni leopard beanie")
      - brand: listing["brand"]
      - department_id: listing["catalog"]["department"]
      - category_id: listing["catalog"]["category"]
      - category_feature_id: listing["catalog"]["category_features"][0] (optional, narrows to the subcategory)
      - condition: listing["condition"] (optional; one of nwt, ret, not_nwt, uln, ug, uf, wlsl, unsp)
      - exclude_listing_id: listing["id"], so the current listing is not returned as its own match

    Args:
        query: Free-text search words describing the item.
        brand: Brand name to restrict to, e.g. "Ganni".
        department_id: Poshmark department id (e.g. Women, Men, Kids).
        category_id: Poshmark category id (e.g. Accessories, Shoes).
        category_feature_id: Poshmark subcategory id (e.g. Belts, Hats).
        condition: Condition code to restrict to.
        min_price: Lowest price to include. Give both min_price and max_price to
            filter by price; a missing one is filled with 0 or a very high ceiling.
        max_price: Highest price to include.
        currency_code: Currency of min_price/max_price, default USD.
        exclude_listing_id: Id of the listing being viewed, removed from results.
        count: How many listings to return (1-20, default 10).
        sort_by: One of best_match, added_desc (newest), price_asc, price_desc.

    Returns:
        dict with:
          listings: list of {id, title, brand, price, original_price, currency, condition,
                    size, department, category, subcategory, seller_username,
                    inventory_status, url, picture_url}
          price_summary: {count, min, max, average, median} over the returned listings
          price_range_used: {min, max, currency} when a price filter was applied
          total_matching_on_poshmark: how many listings matched in total (may be null)
          search_used: the filters that were applied
        On failure returns {"msg": "<error>"}.
    """
    try:
        price_range = None
        if min_price is not None or max_price is not None:
            low = max(0.0, float(min_price)) if min_price is not None else 0.0
            high = float(max_price) if max_price is not None else PRICE_CEILING
            if high < low:
                low, high = high, low
            price_range = (low, high)
        search_request = _build_search_request(
            query, brand, department_id, category_id, category_feature_id,
            condition, price_range, currency_code, sort_by, count,
        )
        result = await _find_listings(search_request, exclude_listing_id)
        if price_range:
            result["price_range_used"] = {
                "min": round(price_range[0], 2), "max": round(price_range[1], 2), "currency": currency_code,
            }
        return result
    except Exception as e:
        return {"msg": f"Could not fetch similar listings: {e}"}


@mcp.tool()
def get_temperature(city: str) -> str:
    """
    Gets the temperature for the given city
    Args:
        city: str

    Returns: str

    """
    return f"The temperature in the given city is 25 degrees Celsius"


if __name__ == '__main__':
    mcp.run(transport="sse", port=8001)
