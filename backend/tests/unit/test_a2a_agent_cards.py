"""Tests for A2A agent card generation."""

import pytest

from app.a2a.agent_cards import build_agent_card, build_all_agent_cards
from app.registry import AgentRegistry


class TestBuildAgentCard:
    def test_card_has_correct_name(self):
        registry = AgentRegistry()
        card = build_agent_card("eva", registry)
        assert card.name == "eva"

    def test_card_has_description_from_registry(self):
        registry = AgentRegistry()
        entry = registry.get("eva")
        card = build_agent_card("eva", registry)
        assert entry.description in card.description

    def test_card_has_skills_from_tool_set(self):
        registry = AgentRegistry()
        card = build_agent_card("eva", registry)
        skill_ids = [s.id for s in card.skills]
        assert "transaction_read" in skill_ids
        assert "bank_data_read" in skill_ids
        assert "chargeback_read" in skill_ids

    def test_card_url_includes_agent_name(self):
        registry = AgentRegistry()
        card = build_agent_card("eva", registry, base_url="http://localhost:8000")
        assert "/a2a/eva" in card.url

    def test_card_capabilities_include_streaming(self):
        registry = AgentRegistry()
        card = build_agent_card("eva", registry)
        assert card.capabilities.streaming is True

    def test_unknown_agent_raises(self):
        registry = AgentRegistry()
        with pytest.raises(KeyError):
            build_agent_card("nonexistent", registry)


class TestBuildAllAgentCards:
    def test_returns_all_four_agents(self):
        registry = AgentRegistry()
        cards = build_all_agent_cards(registry)
        assert set(cards.keys()) == {"ellen", "eva", "ming", "shijing"}

    def test_each_card_is_agent_card_type(self):
        from a2a.types import AgentCard

        registry = AgentRegistry()
        cards = build_all_agent_cards(registry)
        for card in cards.values():
            assert isinstance(card, AgentCard)
