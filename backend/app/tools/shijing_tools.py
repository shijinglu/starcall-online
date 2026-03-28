"""Shijing's tool stubs -- user risk analyst."""

from __future__ import annotations


async def user_profile_read(user_id: str = "default", **kwargs) -> dict:
    return {
        "user_id": user_id,
        "account_age_days": 847,
        "country": "US",
        "email_verified": True,
        "phone_verified": True,
    }


async def user_journey_read(user_id: str = "default", days: int = 30, **kwargs) -> dict:
    return {
        "user_id": user_id,
        "login_count": 23,
        "device_changes": 1,
        "address_changes": 0,
        "avg_session_minutes": 12.3,
    }


async def risk_score_read(user_id: str = "default", **kwargs) -> dict:
    return {
        "user_id": user_id,
        "risk_score": 42,
        "risk_tier": "medium",
        "last_updated": "2026-03-27",
    }
