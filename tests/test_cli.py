from __future__ import annotations

import json
import urllib.request

import pytest

from midplane.models import OpenAIChatProvider, provider_from_env
from midplane.run import main
from midplane.store import SqliteEpisodeStore
from tests.conftest import SAMPLE_DIFF


def test_cli_prints_the_episode_id(tmp_path, capsys):
    diff_path = tmp_path / "change.diff"
    diff_path.write_text(SAMPLE_DIFF)
    database = tmp_path / "episodes.sqlite"
    code = main(["--diff", str(diff_path), "--database", f"sqlite:///{database}", "--repo", "demo"])
    printed = capsys.readouterr().out.strip()
    assert code == 0
    episode = SqliteEpisodeStore(str(database)).get(printed)
    assert episode is not None
    assert episode.source == "dev_guild"
    assert episode.outcome == "success"


def test_cli_missing_diff_returns_2(tmp_path, capsys):
    missing = tmp_path / "nope.diff"
    code = main(["--diff", str(missing)])
    assert code == 2
    assert "diff file not found" in capsys.readouterr().err


def test_provider_from_env_defaults_to_fake(monkeypatch):
    monkeypatch.delenv("MIDPLANE_MODEL_PROVIDER", raising=False)
    provider = provider_from_env({})
    response = provider.complete(model_id="fake-small", prompt="Diff:\n+x = 1\n")
    assert response.model_id == "fake-small"
    assert response.text.startswith("{")


def test_real_provider_requires_a_key():
    with pytest.raises(RuntimeError):
        provider_from_env({"MIDPLANE_MODEL_PROVIDER": "openai"})
    with pytest.raises(RuntimeError):
        OpenAIChatProvider("")


def test_openai_provider_parses_a_mocked_response(monkeypatch):
    body = {
        "choices": [{"message": {"content": "{\"summary\": \"ok\", \"findings\": [{\"severity\": \"info\", \"detail\": \"d\", \"ref\": \"diff\"}], \"confidence\": 0.4}"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }
    payload = json.dumps(body).encode()

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return payload

    def fake_urlopen(request, timeout=0):
        assert request.get_header("Authorization") == "Bearer test-key"
        assert request.full_url.endswith("/chat/completions")
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    response = OpenAIChatProvider("test-key").complete(model_id="gpt-test", prompt="hello")
    assert response.input_tokens == 11
    assert response.output_tokens == 7
    assert response.cost_usd > 0
