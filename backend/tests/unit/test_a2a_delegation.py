"""Tests for inter-agent delegation via A2A."""

import pytest

from app.a2a.delegation import check_delegation_allowed, DelegationError


class TestCheckDelegationAllowed:
    def test_allows_first_delegation(self):
        check_delegation_allowed(source_agent="eva", target_agent="ming", delegation_chain=[], max_depth=2)

    def test_rejects_self_delegation(self):
        with pytest.raises(DelegationError, match="self-delegation"):
            check_delegation_allowed(source_agent="eva", target_agent="eva", delegation_chain=[], max_depth=2)

    def test_rejects_cycle(self):
        with pytest.raises(DelegationError, match="cycle"):
            check_delegation_allowed(source_agent="ming", target_agent="eva", delegation_chain=["eva", "ming"], max_depth=5)

    def test_rejects_depth_exceeded(self):
        with pytest.raises(DelegationError, match="depth"):
            check_delegation_allowed(source_agent="shijing", target_agent="ellen", delegation_chain=["eva", "ming"], max_depth=2)

    def test_allows_at_max_depth(self):
        check_delegation_allowed(source_agent="ming", target_agent="ellen", delegation_chain=["eva"], max_depth=2)
