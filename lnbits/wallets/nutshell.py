# lnbits_wallet_nutshell.py (conceptual scaffold)
from __future__ import annotations

from typing import Optional, Tuple

from lnbits.wallets.nutshell_files.nutshell_client import NutshellClient, encode_checking_id, decode_checking_id


SAT_TO_MSAT = 1000


class NutshellWalletBackend:
    def __init__(self, uds_path: str = "/run/cashu/walletd.sock"):
        self.client = NutshellClient(uds_path=uds_path)

    async def status(self) -> Tuple[bool, str]:
        try:
            b = await self.client.get_balance()
            return True, f"ok (default_mint={b.default_mint}, available={b.available} sat)"
        except Exception as e:
            return False, str(e)

    async def get_balance_msat(self) -> int:
        b = await self.client.get_balance()
        return int(b.available) * SAT_TO_MSAT

    async def create_invoice(self, amount_msat: int, memo: str = "") -> Tuple[str, str]:
        amount_sat = (amount_msat + 999) // 1000  # ceil; LNBits expects exact msat semantics
        b = await self.client.get_balance()

        mint_urls = [b.default_mint] + [m for m in b.per_mint.keys() if m != b.default_mint]

        last_err: Optional[Exception] = None
        for mint_url in mint_urls:
            try:
                q = await self.client.mint_quote(amount=amount_sat, unit="sat", mint_url=mint_url)
                checking_id = encode_checking_id(q.mint_url, q.quote)
                return q.request, checking_id
            except Exception as e:
                last_err = e
                continue

        raise RuntimeError(f"all mints failed to create invoice: {last_err}")

    async def get_invoice_status(self, checking_id: str) -> bool:
        mint_url, quote = decode_checking_id(checking_id)

        st = await self.client.mint_status(quote=quote, mint_url=mint_url)
        if not st.get("paid", False):
            return False

        # finalize (idempotent)
        await self.client.mint_execute(quote=quote, unit="sat", mint_url=mint_url)
        return True

    async def pay_invoice(self, bolt11: str, fee_limit_msat: int) -> Tuple[str, int, str]:
        # returns (payment_hash_or_id, fee_msat, checking_id)
        b = await self.client.get_balance()

        # candidate mints: highest available first
        mint_avails = []
        for mint_url, info in b.per_mint.items():
            mint_avails.append((mint_url, int(info.get("available", 0))))
        mint_avails.sort(key=lambda x: x[1], reverse=True)

        last_err: Optional[Exception] = None
        for mint_url, avail_sat in mint_avails:
            try:
                q = await self.client.melt_quote(invoice=bolt11, unit="sat", mint_url=mint_url)

                need_sat = q.amount + q.fee_reserve
                if need_sat > avail_sat:
                    continue

                if q.fee_reserve * SAT_TO_MSAT > fee_limit_msat:
                    continue

                ex = await self.client.melt_execute(
                    quote=q.quote,
                    invoice=bolt11,
                    fee_reserve=q.fee_reserve,
                    unit="sat",
                    mint_url=mint_url,
                )

                checking_id = encode_checking_id(q.mint_url, q.quote)

                # fee precision: prefer walletd-returned fee_paid if present, else reserve
                fee_sat = int(ex.data.get("fee_paid_sat", ex.data.get("fee_paid", q.fee_reserve)))
                fee_msat = fee_sat * SAT_TO_MSAT

                payment_id = ex.data.get("payment_hash") or ex.data.get("preimage") or q.quote
                return payment_id, fee_msat, checking_id

            except Exception as e:
                last_err = e
                continue

        raise RuntimeError(f"all mints failed to pay invoice: {last_err}")

    async def get_payment_status(self, checking_id: str) -> Tuple[bool, Optional[int]]:
        mint_url, quote = decode_checking_id(checking_id)
        st = await self.client.melt_status(quote=quote, mint_url=mint_url)
        if not st.get("paid", False):
            return False, None

        fee_sat = st.get("fee_paid_sat")
        fee_msat = int(fee_sat) * SAT_TO_MSAT if fee_sat is not None else None
        return True, fee_msat

