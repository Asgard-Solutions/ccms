"""Helcim HTTP client — async, per-tenant credentialed.

Methods map 1:1 to the Helcim API surface we need:
  * `connection_test()` — sanity check, used by the settings "Test" button.
  * `initialize_helcim_pay()` — hosted-checkout session token.
  * `purchase_with_card_token()` — charge a stored card-on-file.
  * `refund(transaction_id, amount)` — refund / partial refund.

Each method takes the structured input → returns a `HelcimResponse` dict
with `(ok, status_code, data, error)`. Callers translate the dict into
HTTP responses + audit events.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from services.billing.helcim import HELCIM_API_BASE, HELCIM_PAY_INIT_URL
from services.billing.helcim.credentials import random_idempotency_key

logger = logging.getLogger("ccms.billing.helcim.client")

DEFAULT_TIMEOUT = 15.0


class HelcimResponse(dict):
    """Tiny helper — a dict with `.ok` semantics."""
    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))


class HelcimClient:
    def __init__(self, api_token: str, *, account_id: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT):
        self._api_token = api_token
        self._account_id = account_id
        self._timeout = timeout

    def _headers(self, *, idempotent: bool = False) -> dict[str, str]:
        h = {
            "accept": "application/json",
            "content-type": "application/json",
            "api-token": self._api_token,
        }
        if idempotent:
            h["idempotency-key"] = random_idempotency_key()
        return h

    async def _request(self, method: str, url: str, *,
                       json: dict | None = None,
                       idempotent: bool = False) -> HelcimResponse:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as cx:
                r = await cx.request(
                    method, url,
                    headers=self._headers(idempotent=idempotent),
                    json=json,
                )
                ok = 200 <= r.status_code < 300
                try:
                    data: Any = r.json()
                except Exception:
                    data = {"raw": r.text}
                logger.info(
                    "helcim.request method=%s url=%s status=%s ok=%s",
                    method, url, r.status_code, ok,
                )
                return HelcimResponse(
                    ok=ok, status_code=r.status_code, data=data,
                    error=None if ok else (data.get("error") or data.get("message") or r.text),
                )
        except httpx.TimeoutException:
            logger.warning("helcim.timeout method=%s url=%s", method, url)
            return HelcimResponse(ok=False, status_code=504,
                                  data=None, error="Helcim API timeout")
        except httpx.HTTPError as e:
            logger.warning("helcim.http_error method=%s url=%s err=%s", method, url, e)
            return HelcimResponse(ok=False, status_code=502,
                                  data=None, error=f"Helcim transport error: {e}")

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    async def connection_test(self) -> HelcimResponse:
        return await self._request("GET", f"{HELCIM_API_BASE}/connection-test")

    async def initialize_helcim_pay(
        self, *, amount: float, currency: str, payment_type: str = "purchase",
        invoice_number: str | None = None,
        customer_code: str | None = None,
        description: str | None = None,
    ) -> HelcimResponse:
        body: dict = {
            "paymentType": payment_type,
            "amount": float(amount),
            "currency": currency,
        }
        if invoice_number:
            body["invoiceNumber"] = invoice_number
        if customer_code:
            body["customerCode"] = customer_code
        if description:
            body["description"] = description
        return await self._request("POST", HELCIM_PAY_INIT_URL, json=body)

    async def purchase_with_card_token(
        self, *, amount: float, currency: str,
        card_token: str, customer_code: str | None = None,
        invoice_number: str | None = None,
        comments: str | None = None,
    ) -> HelcimResponse:
        body: dict = {
            "amount": float(amount),
            "currency": currency,
            "cardData": {"cardToken": card_token},
        }
        if customer_code:
            body["customerCode"] = customer_code
        if invoice_number:
            body["invoiceNumber"] = invoice_number
        if comments:
            body["comments"] = comments
        return await self._request(
            "POST", f"{HELCIM_API_BASE}/payments/purchase",
            json=body, idempotent=True,
        )

    async def refund(
        self, *, transaction_id: str, amount: float | None = None,
        comments: str | None = None,
    ) -> HelcimResponse:
        body: dict = {"transactionId": int(transaction_id)} if str(transaction_id).isdigit() \
            else {"transactionId": transaction_id}
        if amount is not None:
            body["amount"] = float(amount)
        if comments:
            body["comments"] = comments
        return await self._request(
            "POST", f"{HELCIM_API_BASE}/payments/refund",
            json=body, idempotent=True,
        )
