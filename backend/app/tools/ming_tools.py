"""Ming's tool stubs -- fraud investigator."""

from __future__ import annotations


async def id_check(user_id: str = "default", **kwargs) -> dict:
    return {
        "user_id": user_id,
        "identity_verified": True,
        "document_type": "drivers_license",
        "match_confidence": 0.97,
    }


async def async_risk_check(user_id: str = "default", **kwargs) -> dict:
    return {
        "user_id": user_id,
        "risk_signals": ["velocity_spike"],
        "score": 68,
    }


async def fraud_signal_read(user_id: str = "default", **kwargs) -> dict:
    return {
        "user_id": user_id,
        "signals": [
            {
                "type": "device_fingerprint_mismatch",
                "confidence": 0.72,
                "detected_at": "2026-03-25",
            }
        ],
    }
