import logging
from typing import Optional

from fastmcp import FastMCP
import httpx

from posh_offers import fetch_active_offers
from posh_listing_search import (
    POSH_API_BASE,
    HTTP_TIMEOUT_SECONDS,
    PRICE_BAND_PERCENT,
    MAX_QUERY_WORDS,
    DEFAULT_SORT,
    price_band,
    trim_query,
    search_listings,
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
    condition: Optional[str] = None,
    listing_price: Optional[float] = None,
    currency_code: str = "USD",
    exclude_listing_id: Optional[str] = None,
    count: int = 10,
    sort_by: str = DEFAULT_SORT,
):
    """
    Finds other Poshmark listings similar to the one the buyer is looking at,
    optionally limited to a price range.

    Use this when the buyer asks things like "are there similar items?", "show me
    other options like this", "what else is available in this style?", and also for
    price questions such as "is this a good price?", "is this overpriced?",
    "what do similar items cost?". For price questions pass listing_price (the
    current listing's price); the search is then limited to listings priced within
    5% below and 5% above it, and sort_by="price_asc" lists the cheapest first. The
    response includes a price_summary you can compare the current listing against.
    This compares ONLY against other Poshmark listings, never other websites. Only
    listings currently available on Poshmark are returned.

    Take the arguments from the current listing's data:
      - query: AT MOST 5 important words: brand + item type + one or two distinguishing
        details, e.g. "Rachel Roy bodycon dress" or "Ganni leopard beanie". Never the
        full title, and no sizes. Anything past 5 words is cut off.
      - brand: listing["brand"]
      - department_id: listing["catalog"]["department"]
      - category_id: listing["catalog"]["category"]
      - condition: only when the buyer asks for a specific condition
        (one of nwt, ret, not_nwt, uln, ug, uf, wlsl, unsp); otherwise leave it out
      - listing_price: listing["price"], ONLY for price questions; leave it out when
        the buyer just wants similar items
      - exclude_listing_id: listing["id"], so the current listing is not returned as its own match

    The tool fills the list by itself. It searches with every filter first and, if that
    gives fewer than `count` listings, widens the search one filter at a time (condition,
    then brand, then the price band; department and category always stay) and appends
    the broader matches after the exact ones. Each listing says which filters were
    dropped to find it in matched_after_dropping ([] = exact match), and filters_relaxed
    lists everything dropped overall. You do not need to call it again with fewer
    filters.

    Args:
        query: Up to 5 important search words describing the item.
        brand: Brand name to restrict to, e.g. "Ganni".
        department_id: Poshmark department id (e.g. Women, Men, Kids).
        category_id: Poshmark category id (e.g. Accessories, Shoes).
        condition: Condition code to restrict to.
        listing_price: Price of the listing being viewed. When given, only listings
            priced from 5% below to 5% above it are returned.
        currency_code: Currency of listing_price, default USD.
        exclude_listing_id: Id of the listing being viewed, removed from results.
        count: How many listings to return (1-20, default 10).
        sort_by: relevance_v2 (default, same as the Poshmark app), price_asc, price_desc,
            added_desc (newest) or best_match. Use price_asc for price questions. Price
            and date orders are applied to the collected results, so they never reduce
            how many listings are found.

    Returns:
        dict with:
          listings: list of {id, title, brand, price, original_price, currency, condition,
                    size, department, category, subcategory, seller_username,
                    inventory_status, listed_at, url, picture_url, matched_after_dropping},
                    exact matches first (unless a price or date sort was asked for)
          exact_match_count: how many of them matched every filter
          price_summary: {count, min, max, average, median} over the returned listings
          price_range_used: {min, max, currency} when listing_price was given
          total_matching_on_poshmark: how many listings matched in total (may be null)
          filters_relaxed: filters that were dropped to fill the list ([] if none)
          search_used: the filters that were applied in the final attempt
        On failure returns {"msg": "<error>"}.
    """
    try:
        price_range = price_band(listing_price, PRICE_BAND_PERCENT)
        result = await search_listings(
            query=trim_query(query, MAX_QUERY_WORDS),
            brand=brand,
            department_id=department_id,
            category_id=category_id,
            condition=condition,
            price_range=price_range,
            currency_code=currency_code,
            sort_by=sort_by,
            count=count,
            exclude_listing_id=exclude_listing_id,
        )
        if price_range:
            result["listing_price"] = float(listing_price)
            result["price_range_used"] = {
                "min": price_range[0], "max": price_range[1], "currency": currency_code,
                "band_percent": PRICE_BAND_PERCENT,
            }
        return result
    except Exception as e:
        return {"msg": f"Could not fetch similar listings: {e}"}


@mcp.tool
async def get_active_offers(
    post_id: str,
    access_token: Optional[str] = None,
    max_id: Optional[str] = None,
):
    """
    Reads the offers currently open on one listing, for the seller who owns it.

    Use this when the seller asks about offers on the listing they are looking at:
    "any offers on this?", "what have people offered?", "how do the offers compare to
    my price?". It only describes offers. It cannot accept, decline or counter one,
    and it says nothing about whether the seller should.

    Take post_id from the current listing's data: listing["id"].

    Reading offers needs the seller's own access token. The conversation gives you one
    as access_token in the listing data -- pass it straight through. Otherwise the server
    falls back to its POSH_ACCESS_TOKEN environment variable. When neither works the tool
    returns {"msg": "..."} saying so; repeat that to the seller rather than guessing at
    offers.

    Args:
        post_id: Id of the listing whose offers to read.
        access_token: The seller's access token from the listing data, when there is one.
        max_id: Paging cursor from a previous call's next_max_id.

    Returns:
        dict with:
          offers: list of {id, amount, currency, state, status_message,
                  latest_offer_message, created_at, status_changed_at, buyer_username,
                  type, is_bundle, bundle_item_count}
          count: how many offers are open
          single_item_offer_summary: {count, min, max} over offers for this listing
                  alone -- bundle offers are left out because their amount covers
                  several items and must not be compared with this listing's price
          has_bundle_offers: whether any offer in the list is a bundle
          next_max_id: cursor for the next page, when there is one
        On failure returns {"msg": "<reason>"}.

    status_message is already written for display by the server. Use it as it is
    instead of describing the state in your own words.
    """
    try:
        return await fetch_active_offers(post_id, max_id, access_token)
    except Exception as e:
        return {"msg": f"Could not fetch active offers: {e}"}


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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mcp.run(transport="sse", port=8001)
