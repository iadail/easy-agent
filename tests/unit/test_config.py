from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from agent_common.models import Protocol
from agent_config.app import load_config


def test_load_config_expands_environment_variables(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("EA_STORAGE", str(tmp_path / "state"))
    config_path = tmp_path / "easy-agent.yml"
    config_path.write_text(
        """
model:
  provider: deepseek
  protocol: auto
graph:
  entrypoint: coordinator
  agents:
    - name: coordinator
  nodes: []
storage:
  path: ${EA_STORAGE}
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.model.protocol is Protocol.AUTO
    assert Path(config.storage.path) == tmp_path / "state"


