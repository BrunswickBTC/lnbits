# lnbits/wallets/nutshell.py
from __future__ import annotations

import os
from typing import Optional

from loguru import logger

from lnbits.wallets.base import (
    InvoiceResponse,
    PaymentFailedStatus,
    PaymentPendingStatus,
    PaymentResponse,
    PaymentStatus,
    PaymentSuccessStatus,
    StatusResponse,
    Wallet,
)

from lnbits.wallets.nutshell_files.nutshell_client import (
    NutshellClient,
    decode_checking_id,
    encode_checking_id,
)

SAT_TO_MSAT = 1000


class NutshellWallet(Wallet):
    """
    LNbits funding source backed by Cashu Nutshell walletd over a Unix Domain Socket.

    Env var:
      - NUTSHELL_WALLETD_UDS=/run/cashu/walletd.sock
    """

    def __init__(self, uds_path: Optional[str] = None) -> None:
        super().__init__()
        self.uds_path = uds_path or os.environ.get(
            "NUTSHELL_WALLETD_UDS", "/run/cashu/walletd.sock"
        )
        self.client = NutshellClient(uds_path=self.uds_path)

    async def cleanup(self):
        try:
            await self.client.aclose()
        except Exception as e:
            logger.warning(f"NutshellWallet cleanup: {e}")

    async def status(self) -> StatusResponse:
        try:
            b = await self.client.get_balance()
            return StatusResponse(None, int(b.available) * SAT_TO_MSAT)
        except Exception as e:
            return StatusResponse(str(e), 0)

    async def create_invoice(
        self,
        amount: int,  # sat (LNbits convention)
        memo: str | None = None,
        description_hash: bytes | None = None,
        unhashed_description: bytes | None = None,
        **kwargs,
    ) -> InvoiceResponse:
        # walletd endpoints currently only take memo-like text; LNbits may provide desc_hash.
        # Prefer memo; ignore desc_hash for now (safe for most LNbits usage).
        amount_sat = int(amount)
        memo = memo or ""

        try:
            b = await self.client.get_balance()
            mint_urls = [b.default_mint] + [
                m for m in b.per_mint.keys() if m != b.default_mint
            ]

            last_err: Optional[Exception] = None
            for mint_url in mint_urls:
                try:
                    q = await self.client.mint_quote(
                        amount=amount_sat, unit="sat", mint_url=mint_url
                    )
                    checking_id = encode_checking_id(q.mint_url, "sat", q.quote)
                    # LNbits uses checking_id to later ask "paid?"
                    self.pending_invoices.append(checking_id)
                    return InvoiceResponse(
                        ok=True, checking_id=checking_id, payment_request=q.request
                    )
                except Exception as e:
                    last_err = e
                    continue

            return InvoiceResponse(ok=False, error_message=f"{last_err}")
        except Exception as e:
            return InvoiceResponse(ok=False, error_message=str(e))


    async def get_invoice_status(self, checking_id: str) -> PaymentStatus:
        try:
            mint_url, unit, quote = decode_checking_id(checking_id)
            st = await self.client.mint_status(quote=quote, mint_url=mint_url)

            #if st.get("paid") is True:
            if st.get("status") in {"claimable"}:
                # finalize mint (idempotent)
                try:
                    await self.client.mint_execute(quote=quote, unit=unit, mint_url=mint_url)
                    return PaymentSuccessStatus()
                except Exception as e:
                    # Only treat as success if walletd says it's already claimed/minted.
                    msg = str(e).lower()
                    if "already" in msg and ("claimed" in msg or "mint" in msg):
                        return PaymentSuccessStatus()

                    logger.warning(f"mint_execute failed for {checking_id}: {e}")
                    return PaymentPendingStatus()

            # If walletd returns an explicit failure state, honor it.
            if st.get("failed") is True or st.get("status") in {"failed", "expired", "canceled"}:
                return PaymentFailedStatus()

            return PaymentPendingStatus()
        except Exception as e:
            logger.warning(f"NutshellWallet get_invoice_status error: {e}")
            return PaymentPendingStatus()


    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> PaymentResponse:
        try:
            b = await self.client.get_balance()

            mint_avails = []
            for mint_url, info in b.per_mint.items():
                mint_avails.append((mint_url, int(info.get("available", 0))))
            mint_avails.sort(key=lambda x: x[1], reverse=True)

            last_err: Optional[Exception] = None
            for mint_url, avail_sat in mint_avails:
                try:
                    q = await self.client.melt_quote(
                        invoice=bolt11, unit="sat", mint_url=mint_url
                    )
                    need_sat = int(q.amount) + int(q.fee_reserve)
                    if need_sat > avail_sat:
                        continue
                    if int(q.fee_reserve) * SAT_TO_MSAT > int(fee_limit_msat):
                        continue

                    ex = await self.client.melt_execute(
                        quote=q.quote,
                        invoice=bolt11,
                        fee_reserve=q.fee_reserve,
                        unit="sat",
                        mint_url=mint_url,
                    )
                    checking_id = encode_checking_id(q.mint_url, "sat", q.quote)

                    fee_sat = ex.data.get("fee_paid_sat", ex.data.get("fee_paid", q.fee_reserve))
                    fee_msat = int(fee_sat) * SAT_TO_MSAT

                    preimage = ex.data.get("preimage")
                    return PaymentResponse(
                        ok=True, checking_id=checking_id, fee_msat=fee_msat, preimage=preimage
                    )
                except Exception as e:
                    last_err = e
                    continue

            return PaymentResponse(ok=False, error_message=f"{last_err}")
        except Exception as e:
            return PaymentResponse(ok=False, error_message=str(e))

    async def get_payment_status(self, checking_id: str) -> PaymentStatus:
        try:
            mint_url, unit, quote = decode_checking_id(checking_id)
            st = await self.client.melt_status(quote=quote, mint_url=mint_url)

            if st.get("paid") is True:
                fee_sat = st.get("fee_paid_sat")
                fee_msat = int(fee_sat) * SAT_TO_MSAT if fee_sat is not None else None
                preimage = st.get("preimage")
                return PaymentSuccessStatus(fee_msat=fee_msat, preimage=preimage)

            if st.get("failed") is True or st.get("status") in {"failed", "expired", "canceled"}:
                return PaymentFailedStatus()

            return PaymentPendingStatus()
        except Exception as e:
            logger.warning(f"NutshellWallet get_payment_status error: {e}")
            return PaymentPendingStatus()

