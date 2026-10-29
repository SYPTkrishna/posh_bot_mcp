"""
Updating one listing on the seller's behalf, for the seller side of POSHBOT.

The Poshmark server exposes this at POST /api/posts/:post_id (goshposh/server
lib/api_app.rb, `post '/posts/:post_id'`). The route reads the new attributes from a
`post` form field holding JSON, and needs the seller's own access token.

Two things about that route shape this module:

- It expects the editable attributes to be sent back together, the way the apps do.
  Fields the server treats as optional (brand_id, seller shipping discount) are *unset*
  when the payload omits them, so a payload with only the changed field would strip
  them off the listing. update_listing therefore fetches the current listing first and
  echoes its editable attributes back with only the requested fields replaced.
- Pictures are left alone unless both cover_shot and pictures are sent
  (post_service.rb process_pictures_v2), so this module never sends either.
"""

import json
import os
from typing import Optional

import httpx

from posh_listing_search import POSH_API_BASE, HTTP_TIMEOUT_SECONDS, compact_listing, money
from posh_offers import unwrap_access_token

ACCESS_TOKEN_ENV_VAR = "POSH_ACCESS_TOKEN"

# Server limits: GoshPosh::Platform::Posts::PostV2 in lib/platform/posts/post.rb
MAX_TITLE_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 1500

EDITABLE_FIELDS = ("title", "description", "price", "original_price")

NO_TOKEN_MESSAGE = (
    "No access token for the seller, so the listing cannot be updated. Pass the seller's "
    f"access_token, or set {ACCESS_TOKEN_ENV_VAR} on the MCP server."
)
NOT_AUTHORISED_MESSAGE = (
    "Not authorised to update this listing. The token belongs to a different user than the "
    "listing's seller, or it has expired. The listing is unchanged."
)


def _whole_dollars(value, field: str) -> str:
    """Poshmark prices are whole currency units; the server rejects anything else."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number")
    if amount <= 0:
        raise ValueError(f"{field} must be greater than zero")
    if amount != int(amount):
        raise ValueError(f"{field} must be a whole dollar amount (no cents)")
    return str(int(amount))


def validate_changes(title, description, price, original_price) -> dict:
    """Returns {field: value} for the fields the caller wants changed, or raises ValueError."""
    changes: dict = {}
    if title is not None:
        title = title.strip()
        if not title:
            raise ValueError("title must not be empty")
        if len(title) > MAX_TITLE_LENGTH:
            raise ValueError(f"title is {len(title)} characters; the maximum is {MAX_TITLE_LENGTH}")
        changes["title"] = title
    if description is not None:
        description = description.strip()
        if not description:
            raise ValueError("description must not be empty")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValueError(
                f"description is {len(description)} characters; the maximum is {MAX_DESCRIPTION_LENGTH}"
            )
        changes["description"] = description
    if price is not None:
        changes["price"] = _whole_dollars(price, "price")
    if original_price is not None:
        changes["original_price"] = _whole_dollars(original_price, "original_price")
    if not changes:
        raise ValueError("Nothing to change: pass at least one of title, description, price, original_price")
    return changes


async def fetch_post(post_id: str, token: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        return await client.get(f"{POSH_API_BASE}/posts/{post_id}", params={"access_token": token})


def build_update_payload(current: dict, changes: dict) -> dict:
    """
    The `post` JSON the server expects: the listing's current editable attributes with
    the requested changes applied. Echoing the unchanged ones is what keeps the server
    from unsetting brand_id and the shipping discount.
    """
    price_amount = current.get("price_amount") or {}
    currency = price_amount.get("currency_code") or "USD"
    catalog = current.get("catalog") or {}
    inventory = current.get("inventory") or {}

    payload: dict = {
        "title": changes.get("title", current.get("title")),
        "description": changes.get("description", current.get("description")),
        "brand": current.get("brand"),
        "catalog": {
            "department": catalog.get("department"),
            "category": catalog.get("category"),
            "category_features": catalog.get("category_features") or [],
        },
        "colors": [c.get("name") for c in (current.get("colors") or []) if c.get("name")],
        "inventory": {
            "status": inventory.get("status"),
            "multi_item": inventory.get("multi_item"),
            "size_quantities": inventory.get("size_quantities") or [],
        },
        "style_tags": current.get("style_tags") or [],
    }
    if current.get("condition"):
        payload["condition"] = current["condition"]
    if current.get("seller_shipping_discount_id"):
        payload["seller_shipping_discount"] = {"id": current["seller_shipping_discount_id"]}

    if "price" in changes:
        payload["price_amount"] = {"val": changes["price"], "currency_code": currency}
    elif money(price_amount) is not None:
        payload["price_amount"] = {"val": price_amount.get("val"), "currency_code": currency}

    original = current.get("original_price_amount") or {}
    if "original_price" in changes:
        payload["original_price_amount"] = {"val": changes["original_price"], "currency_code": currency}
    elif money(original) is not None:
        payload["original_price_amount"] = {"val": original.get("val"), "currency_code": original.get("currency_code") or currency}

    return {k: v for k, v in payload.items() if v is not None}


def _server_error_message(response: httpx.Response) -> str:
    try:
        err = response.json().get("error") or {}
        return err.get("user_message") or err.get("message") or response.text[:200]
    except Exception:
        return response.text[:200]


def _snapshot(post: dict) -> dict:
    return {
        "title": post.get("title"),
        "description": post.get("description"),
        "price": money(post.get("price_amount")),
        "original_price": money(post.get("original_price_amount")),
    }


async def update_listing(
    post_id: str,
    access_token: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    price=None,
    original_price=None,
) -> dict:
    """
    Applies the given changes to one listing and reports each field as before -> after.
    Returns {"msg": ...} and changes nothing when the request is invalid, the token is
    missing or wrong, or the server rejects the update.
    """
    try:
        changes = validate_changes(title, description, price, original_price)
    except ValueError as e:
        return {"msg": str(e)}

    token = unwrap_access_token(access_token or os.environ.get(ACCESS_TOKEN_ENV_VAR))
    if not token:
        return {"msg": NO_TOKEN_MESSAGE}

    before_resp = await fetch_post(post_id, token)
    if before_resp.status_code in (401, 403):
        return {"msg": NOT_AUTHORISED_MESSAGE}
    if before_resp.status_code == 404:
        return {"msg": f"Listing {post_id} was not found. Nothing was changed."}
    if before_resp.status_code != 200:
        return {"msg": f"Could not read the listing before updating it (HTTP {before_resp.status_code}). Nothing was changed."}
    before = before_resp.json()
    before = before.get("data") if isinstance(before.get("data"), dict) else before

    payload = build_update_payload(before, changes)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{POSH_API_BASE}/posts/{post_id}",
            params={"access_token": token},
            data={"post": json.dumps(payload), "app_type": "web"},
        )
    if response.status_code in (401, 403):
        return {"msg": NOT_AUTHORISED_MESSAGE}
    if response.status_code != 200:
        return {"msg": f"The server rejected the update: {_server_error_message(response)} The listing is unchanged."}

    after_resp = await fetch_post(post_id, token)
    after = after_resp.json() if after_resp.status_code == 200 else {}
    after = after.get("data") if isinstance(after.get("data"), dict) else after

    snap_before, snap_after = _snapshot(before), _snapshot(after)
    report = {}
    for field in changes:
        report[field] = {"before": snap_before.get(field), "after": snap_after.get(field)}
    not_applied = [f for f in changes if snap_before.get(f) == snap_after.get(f)]

    result = {
        "updated": not not_applied,
        "changes": report,
        "listing": compact_listing(after) if after else None,
    }
    if not_applied:
        result["msg"] = (
            "The server accepted the request but these fields read back unchanged: "
            + ", ".join(not_applied)
        )
    return result
