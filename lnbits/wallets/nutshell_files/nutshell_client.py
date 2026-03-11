from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx


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
    unit: str
    quote: str
    request: str  # bolt11
    amount: int


@dataclass(frozen=True)
class MintStatus:
    mint_url: str
    unit: str
    quote: str
    status: str


@dataclass(frozen=True)
class MintExecuteResult:
    mint_url: str
    unit: str
    quote: str
    status: str
    data: Dict[str, Any]


@dataclass(frozen=True)
class MeltQuote:
    mint_url: str
    unit: str
    payment_hash: str
    amount: int
    fee_reserve: int


@dataclass(frozen=True)
class MeltExecuteResult:
    mint_url: str
    unit: str
    payment_hash: str
    fee_paid_sat: Optional[int]
    preimage: Optional[str]
    status: str
    data: Dict[str, Any]


@dataclass(frozen=True)
class MeltStatus:
    mint_url: str
    unit: str
    payment_hash: str
    fee_paid_sat: Optional[int]
    preimage: Optional[str]
    status: str


class NutshellClient:
    """
    Talks to walletd over Unix Domain Socket using HTTP.
    Assumes walletd serves /v1/* over UDS.
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
            raise NutshellError(f"{method} {path} -> {r.status_code}: {r.text}")
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.text

    async def get_balance(self, unit: Optional[str] = None) -> Balance:
        path = "/v1/balance"
        if unit:
            path = f"/v1/balance?unit={unit}"
        j = await self._req("GET", path)
        return Balance(
            wallet=j["wallet"],
            unit=j["unit"],
            available=int(j["available"]),
            balance=int(j["balance"]),
            per_mint=dict(j.get("per_mint", {})),
            default_mint=j["default_mint"],
        )

    async def mint_quote(
        self,
        amount: int,
        unit: str = "sat",
        mint_url: Optional[str] = None,
        memo: Optional[str] = None,
    ) -> MintQuote:
        payload = {"amount": int(amount), "unit": unit}
        if mint_url:
            payload["mint_url"] = mint_url
        if memo is not None:
            payload["memo"] = memo
        j = await self._req("POST", "/v1/mint/quote", json=payload)
        return MintQuote(
            mint_url=j["mint_url"],
            unit=j["unit"],
            quote=j["quote"],
            request=j["request"],
            amount=int(j["amount"]),
        )

    async def mint_status(
        self,
        quote: str,
        unit: str = "sat",
        mint_url: Optional[str] = None,
    ) -> MintStatus:
        payload = {"quote": quote, "unit": unit}
        if mint_url:
            payload["mint_url"] = mint_url
        j = await self._req("POST", "/v1/mint/status", json=payload)
        return MintStatus(
            mint_url=j["mint_url"],
            unit=j["unit"],
            quote=j["quote"],
            status=j["status"],
        )

    async def mint_execute(
        self,
        quote: str,
        unit: str = "sat",
        mint_url: Optional[str] = None,
    ) -> MintExecuteResult:
        payload = {"quote": quote, "unit": unit}
        if mint_url:
            payload["mint_url"] = mint_url
        j = await self._req("POST", "/v1/mint/execute", json=payload)
        return MintExecuteResult(
            mint_url=j["mint_url"],
            unit=j["unit"],
            quote=j["quote"],
            status=j["status"],
            data=j,
        )

    async def melt_quote(
        self,
        invoice: str,
        unit: str = "sat",
        mint_url: Optional[str] = None,
    ) -> MeltQuote:
        payload = {"invoice": invoice, "unit": unit}
        if mint_url:
            payload["mint_url"] = mint_url
        j = await self._req("POST", "/v1/melt/quote", json=payload)
        return MeltQuote(
            mint_url=j["mint_url"],
            unit=j["unit"],
            payment_hash=j["payment_hash"],
            amount=int(j["amount"]),
            fee_reserve=int(j["fee_reserve"]),
        )

    async def melt_execute(self, payment_hash: str) -> MeltExecuteResult:
        payload = {"payment_hash": payment_hash}
        j = await self._req("POST", "/v1/melt/execute", json=payload)
        return MeltExecuteResult(
            mint_url=j["mint_url"],
            unit=j["unit"],
            payment_hash=j["payment_hash"],
            fee_paid_sat=None if j.get("fee_paid_sat") is None else int(j["fee_paid_sat"]),
            preimage=j.get("preimage"),
            status=j["status"],
            data=j,
        )

    async def melt_status(self, payment_hash: str) -> MeltStatus:
        j = await self._req("GET", f"/v1/melt/status/{payment_hash}")
        return MeltStatus(
            mint_url=j["mint_url"],
            unit=j["unit"],
            payment_hash=j["payment_hash"],
            fee_paid_sat=None if j.get("fee_paid_sat") is None else int(j["fee_paid_sat"]),
            preimage=j.get("preimage"),
            status=j["status"],
        )


def encode_mint_checking_id(mint_url: str, unit: str, quote: str) -> str:
    return f"{mint_url}|{unit}|{quote}"


def decode_mint_checking_id(checking_id: str) -> Tuple[str, str, str]:
    mint_url, unit, quote = checking_id.split("|", 2)
    return mint_url, unit, quote
