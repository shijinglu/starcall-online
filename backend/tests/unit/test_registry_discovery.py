"""Tests for folder-based agent registry discovery."""

import textwrap
from pathlib import Path

import pytest

from app.models import AgentRegistryEntry
from app.registry import _discover_agents, _load_agent_from_folder


@pytest.fixture()
def agent_dir(tmp_path: Path) -> Path:
    """Create a minimal valid agent folder."""
    d = tmp_path / "test_agent"
    d.mkdir()
    (d / "agent.yaml").write_text(
        textwrap.dedent("""\
            name: test_agent
            description: "A test agent"
            voice_id: en-US-Journey-F
            speaker_id: 99
            tool_set:
              - tool_a
              - tool_b
        """)
    )
    (d / "prompt.txt").write_text("You are a test agent.")
    skills_dir = d / ".claude" / "skills" / "test_agent"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("---\nname: test\n---\n")
    return d


class TestLoadAgentFromFolder:
    def test_loads_valid_agent(self, agent_dir: Path):
        entry = _load_agent_from_folder(agent_dir)
        assert entry.name == "test_agent"
        assert entry.description == "A test agent"
        assert entry.voice_id == "en-US-Journey-F"
        assert entry.speaker_id == 99
        assert entry.tool_set == ["tool_a", "tool_b"]
        assert entry.system_prompt == "You are a test agent."
        assert entry.agent_dir == agent_dir

    def test_missing_prompt_uses_empty(self, agent_dir: Path):
        (agent_dir / "prompt.txt").unlink()
        entry = _load_agent_from_folder(agent_dir)
        assert entry.system_prompt == ""

    def test_missing_yaml_key_raises(self, agent_dir: Path):
        (agent_dir / "agent.yaml").write_text("name: incomplete\n")
        with pytest.raises(ValueError, match="missing keys"):
            _load_agent_from_folder(agent_dir)

    def test_missing_yaml_file_raises(self, tmp_path: Path):
        empty = tmp_path / "empty_agent"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            _load_agent_from_folder(empty)


class TestDiscoverAgents:
    def test_discovers_agents_from_dir(self, agent_dir: Path):
        # agent_dir is inside tmp_path; discover from tmp_path
        agents = _discover_agents(agent_dir.parent)
        assert "test_agent" in agents
        assert isinstance(agents["test_agent"], AgentRegistryEntry)

    def test_skips_folders_without_yaml(self, tmp_path: Path):
        (tmp_path / "no_yaml").mkdir()
        agents = _discover_agents(tmp_path)
        assert agents == {}

    def test_skips_malformed_yaml(self, tmp_path: Path):
        bad = tmp_path / "bad_agent"
        bad.mkdir()
        (bad / "agent.yaml").write_text("name: incomplete\n")
        agents = _discover_agents(tmp_path)
        assert agents == {}

    def test_empty_dir_returns_empty(self, tmp_path: Path):
        agents = _discover_agents(tmp_path)
        assert agents == {}

    def test_agent_dir_is_set(self, agent_dir: Path):
        agents = _discover_agents(agent_dir.parent)
        entry = agents["test_agent"]
        assert entry.agent_dir == agent_dir


class TestBuiltInRegistryLoads:
    """Smoke test that the real AGENT_REGISTRY loaded at import time."""

    def test_has_four_agents(self):
        from app.registry import AGENT_REGISTRY

        assert set(AGENT_REGISTRY) == {"ellen", "eva", "ming", "shijing"}

    def test_agents_have_agent_dir(self):
        from app.registry import AGENT_REGISTRY

        for name, entry in AGENT_REGISTRY.items():
            # When loaded from agents/ folder, agent_dir should be set
            assert entry.agent_dir is not None, f"{name} has no agent_dir"
