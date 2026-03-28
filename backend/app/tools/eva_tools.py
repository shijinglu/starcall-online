"""Eva's tool stubs -- financial analyst."""

from __future__ import annotations


async def transaction_read(user_id: str = "default", days: int = 30, **kwargs) -> dict:
    return {
        "user_id": user_id,
        "transaction_count": 18,
        "total_spend_usd": 2340.50,
        "largest_transaction_usd": 499.99,
        "flagged_count": 1,
    }


async def bank_data_read(user_id: str = "default", **kwargs) -> dict:
    return {
        "user_id": user_id,
        "bank": "Chase",
        "account_type": "checking",
        "balance_usd": 4820.33,
        "account_age_days": 1240,
    }


async def chargeback_read(user_id: str = "default", **kwargs) -> dict:
    return {
        "user_id": user_id,
        "chargeback_count_12m": 0,
        "dispute_count_12m": 1,
    }
