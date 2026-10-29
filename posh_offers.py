"""
Active offers on one listing, for the seller side of POSHBOT.

The Poshmark server exposes them at GET /api/posts/:post_id/offers/active
(goshposh/server lib/api_app.rb, `get '/posts/:post_id/offers/active'`). That route
reads the offers against the caller's own identity, so unlike listing search it needs
the seller's access token and answers 401 without one.
"""

import base64
import json
import os
from typing import Optional

import httpx

from posh_listing_search import POSH_API_BASE, HTTP_TIMEOUT_SECONDS, money

ACCESS_TOKEN_ENV = "NmFkYjMzODQ1MWQyMjc0ZmUzMmQyZmZjfDE4MjQ3MzkzODN8MC4yfDB8NmFlMjFjYjczZGJiZDM3ZmExMmFiY2JlfDB8MXwwfDB8bnV1bHhfcnpNbnVkTGJxMVB2NUJXYWxURndMdVc4b3VOTVpsYzBKTkQ2VQ"

NO_TOKEN_MESSAGE = (
    "This MCP server has no Poshmark access token, so it cannot read offers. "
    f"Set {ACCESS_TOKEN_ENV} on the server to a token for the seller who owns the listing."
)

NOT_AUTHORISED_MESSAGE = (
    "Not authorised to read the offers on this listing. The token the server holds "
    "belongs to a different user, or it has expired."
)


def unwrap_access_token(token: Optional[str]) -> Optional[str]:
    """
    Takes the token the API actually wants out of whatever the caller had to hand.

    Signing in gives the client a JWT whose payload carries the real access token as an
    "access_token" claim. The platform rejects the JWT itself -- sending the whole thing
    answers 401 -- so unwrap it when that is what arrived, and pass anything else through
    untouched.
    """
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return token
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return token
    inner = claims.get("access_token")
    return inner if isinstance(inner, str) and inner else token


def compact_offer(offer: dict) -> dict:
    """Keeps the fields the seller prompt is allowed to talk about, and nothing else."""
    listings_info = offer.get("listings_info") or []
    buyer = offer.get("buyer_info") or {}
    # A live offer carries no top-level amount -- the figure the buyer last named is on
    # latest_offer_message, so read that before falling back.
    latest = offer.get("latest_offer_message") or {}
    amount_obj = offer.get("amount") or latest.get("amount") or {}
    amount = money(offer.get("amount")) or money(latest.get("amount"))
    if amount is None and isinstance(latest.get("offer_price"), (int, float)):
        amount = float(latest["offer_price"])
    # The listing's own price, so the seller can be told the gap without it being guessed.
    listing_price = money((listings_info[0] or {}).get("price_amount")) if listings_info else None
    # A bundle offer covers several items, so its amount must never be read as a price
    # for this one listing.
    is_bundle = bool(offer.get("bundle_id")) or len(listings_info) > 1

    return {
        "id": offer.get("id"),
        "amount": amount,
        "currency": amount_obj.get("currency_code"),
        "listing_price": listing_price,
        "state": offer.get("state"),
        # Already framed for display by the server; the prompt tells POSHBOT to use it as-is.
        "status_message": offer.get("status_message"),
        "buyer_message": (latest.get("message") or None),
        "created_at": offer.get("created_at"),
        "status_changed_at": offer.get("status_changed_at"),
        "buyer_username": buyer.get("username") or buyer.get("display_handle"),
        "type": offer.get("type"),
        "is_bundle": is_bundle,
        "bundle_item_count": len(listings_info) if is_bundle else 1,
    }


async def fetch_active_offers(
    post_id: str,
    max_id: Optional[str] = None,
    access_token: Optional[str] = None,
) -> dict:
    token = unwrap_access_token(access_token or os.environ.get(ACCESS_TOKEN_ENV))
    if not token:
        return {"msg": NO_TOKEN_MESSAGE}

    # The platform reads the token from the query string; as a header it answers 401.
    params = {"access_token": token}
    if max_id:
        params["max_id"] = max_id

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = await client.get(
            f"{POSH_API_BASE}/posts/{post_id}/offers/active", params=params
        )

    if response.status_code in (401, 403):
        print("Unauthorized or forbidden")
        return {"msg": NOT_AUTHORISED_MESSAGE}
    if response.status_code != 200:
        print("HTTP error:", response.status_code)
        return {"msg": f"Could not read offers for this listing (HTTP {response.status_code})."}

    body = response.json()
    offers = [compact_offer(offer) for offer in (body.get("data") or [])]
    amounts = [offer["amount"] for offer in offers if offer["amount"] is not None and not offer["is_bundle"]]

    return {
        "offers": offers,
        "count": len(offers),
        # Single-item offers only: a bundle amount is not comparable with this listing's price.
        "single_item_offer_summary": {
            "count": len(amounts),
            "min": min(amounts) if amounts else None,
            "max": max(amounts) if amounts else None,
        },
        "has_bundle_offers": any(offer["is_bundle"] for offer in offers),
        "next_max_id": (body.get("meta") or {}).get("next_max_id"),
    }
