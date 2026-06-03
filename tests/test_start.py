import argparse
import importlib.util
from pathlib import Path

START_PATH = Path(__file__).resolve().parents[1] / "start.py"
spec = importlib.util.spec_from_file_location("alphaengine_start", START_PATH)
assert spec is not None
assert spec.loader is not None
start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start)


def test_env_file_defines_market_provider(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# ALPHA_MARKET_DATA_PROVIDER=mock\nALPHA_MARKET_DATA_PROVIDER=hybrid\n",
        encoding="utf-8",
    )

    assert start.env_file_defines_market_provider(env_file) is True


def test_apply_market_provider_defaults_to_hybrid(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(start, "ROOT_DIR", tmp_path)
    args = argparse.Namespace(provider=None)
    env: dict[str, str] = {}

    provider = start.apply_market_provider(args, env)

    assert provider == "hybrid"
    assert env["ALPHA_MARKET_DATA_PROVIDER"] == "hybrid"
