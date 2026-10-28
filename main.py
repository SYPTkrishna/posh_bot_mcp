from typing import Optional

from fastmcp import FastMCP
import httpx

from posh_listing_search import (
    POSH_API_BASE,
    HTTP_TIMEOUT_SECONDS,
    build_search_request,
    normalize_price_range,
    find_listings,
)

mcp = FastMCP("PoshBot MCP")


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
        price_range = normalize_price_range(min_price, max_price)
        search_request = build_search_request(
            query=query,
            brand=brand,
            department_id=department_id,
            category_id=category_id,
            category_feature_id=category_feature_id,
            condition=condition,
            price_range=price_range,
            currency_code=currency_code,
            sort_by=sort_by,
            count=count,
        )
        result = await find_listings(search_request, exclude_listing_id)
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
