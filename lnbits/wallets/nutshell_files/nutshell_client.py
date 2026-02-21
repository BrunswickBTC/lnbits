# nutshell_client.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx
import base64
import json


class NutshellError(RuntimeError):
    pass


@dataclass(frozen=True)
class Balance:
    wallet: str
    unit: str
    available: int
    balance: int
    per_mint: Dict[str, Any]
    default_mint: str


@dataclass(frozen=True)
class MintQuote:
    mint_url: str
    quote: str
    request: str  # bolt11
    amount: int
    unit: str


@dataclass(frozen=True)
class MeltQuote:
    mint_url: str
    quote: str
    amount: int
    fee_reserve: int
    unit: str


@dataclass(frozen=True)
class ExecuteResult:
    mint_url: str
    quote: str
    status: str  # "paid"|"pending"|"failed"|etc (walletd-defined)
    data: Dict[str, Any]


class NutshellClient:
    """
    Talks to walletd over Unix Domain Socket using HTTP.
    Assumes walletd serves paths like /v1/balance, /v1/mint/quote, etc.
    """

    def __init__(
        self,
        uds_path: str = "/run/cashu/walletd.sock",
        base_url: str = "http://walletd",
        timeout_s: float = 30.0,
    ):
        self.uds_path = uds_path
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_s)

        transport = httpx.AsyncHTTPTransport(uds=self.uds_path)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            timeout=self.timeout,
            headers={"accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _req(self, method: str, path: str, json: Optional[dict] = None) -> Any:
        r = await self._client.request(method, path, json=json)
        if r.status_code >= 400:
            # walletd should return structured errors; preserve body for diagnostics
            raise NutshellError(f"{method} {path} -> {r.status_code}: {r.text}")
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.text

    async def get_balance(self) -> Balance:
        j = await self._req("GET", "/v1/balance")
        return Balance(
            wallet=j["wallet"],
            unit=j["unit"],
            available=int(j["available"]),
            balance=int(j["balance"]),
            per_mint=dict(j.get("per_mint", {})),
            default_mint=j["default_mint"],
        )

    async def mint_quote(self, amount: int, unit: str = "sat", mint_url: Optional[str] = None) -> MintQuote:
        payload = {"amount": int(amount), "unit": unit}
        if mint_url:
            payload["mint_url"] = mint_url
        j = await self._req("POST", "/v1/mint/quote", json=payload)
        return MintQuote(
            mint_url=j["mint_url"],
            quote=j["quote"],
            request=j["request"],
            amount=int(j["amount"]),
            unit=j["unit"],
        )

    async def mint_execute(self, quote: str, unit: str = "sat", mint_url: Optional[str] = None) -> ExecuteResult:
        payload = {"quote": quote, "unit": unit}
        if mint_url:
            payload["mint_url"] = mint_url
        j = await self._req("POST", "/v1/mint/execute", json=payload)
        return ExecuteResult(
            mint_url=j.get("mint_url", mint_url or ""),
            quote=j.get("quote", quote),
            status=j.get("status", "unknown"),
            data=j,
        )

    async def melt_quote(self, invoice: str, unit: str = "sat", mint_url: Optional[str] = None) -> MeltQuote:
        payload = {"invoice": invoice, "unit": unit}
        if mint_url:
            payload["mint_url"] = mint_url
        j = await self._req("POST", "/v1/melt/quote", json=payload)
        return MeltQuote(
            mint_url=j["mint_url"],
            quote=j["quote"],
            amount=int(j["amount"]),
            fee_reserve=int(j["fee_reserve"]),
            unit=j["unit"],
        )

    async def melt_execute(
        self,
        quote: str,
        invoice: str,
        fee_reserve: int,
        unit: str = "sat",
        mint_url: Optional[str] = None,
    ) -> ExecuteResult:
        payload = {"quote": quote, "invoice": invoice, "fee_reserve": int(fee_reserve), "unit": unit}
        if mint_url:
            payload["mint_url"] = mint_url
        j = await self._req("POST", "/v1/melt/execute", json=payload)
        return ExecuteResult(
            mint_url=j.get("mint_url", mint_url or ""),
            quote=j.get("quote", quote),
            status=j.get("status", "unknown"),
            data=j,
        )

    # Optional: if you add explicit status endpoints in walletd
    async def mint_status(self, quote: str, mint_url: Optional[str] = None) -> Dict[str, Any]:
        payload = {"quote": quote}
        if mint_url:
            payload["mint_url"] = mint_url
        return await self._req("POST", "/v1/mint/status", json=payload)

    async def melt_status(self, quote: str, mint_url: Optional[str] = None) -> Dict[str, Any]:
        payload = {"quote": quote}
        if mint_url:
            payload["mint_url"] = mint_url
        return await self._req("POST", "/v1/melt/status", json=payload)


def encode_checking_id(mint_url: str, unit: str, quote: str) -> str:
    payload = {"m": mint_url, "u": unit, "q": quote}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    # store unpadded base64url (cleaner, still decodable)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_checking_id(checking_id: str) -> Tuple[str, str, str]:
    s = (checking_id or "").strip()

    # Legacy: 2-field or 3-field pipe encoding
    if "|" in s:
        parts = s.split("|")
        if len(parts) == 2:
            mint_url, quote = parts
            return mint_url, "sat", quote
        if len(parts) == 3:
            mint_url, unit, quote = parts
            return mint_url, unit, quote
        raise ValueError(f"Invalid legacy checking_id (pipe parts={len(parts)}): {checking_id!r}")

    # New: base64url(JSON), accept missing padding
    try:
        pad = "=" * (-len(s) % 4)
        raw = base64.urlsafe_b64decode((s + pad).encode("ascii"))
        obj = json.loads(raw.decode("utf-8"))

        # accept either short or long keys
        mint_url = obj.get("m") or obj.get("mint_url")
        unit = obj.get("u") or obj.get("unit") or "sat"
        quote = obj.get("q") or obj.get("quote")

        if not mint_url or not quote:
            raise KeyError("missing mint_url/quote")
        return mint_url, unit, quote
    except Exception as e:
        raise ValueError(f"Invalid checking_id: {checking_id!r}") from e

