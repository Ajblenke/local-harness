import configparser
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CONTEXT = 131072


def test_local_model_configs_share_context_and_contain_no_cloud_credentials():
    parser = configparser.ConfigParser(interpolation=None)
    router_text = (ROOT / "config" / "llama-router" / "models.ini").read_text()
    parser.read_string("[router]\n" + router_text)
    pi_models = json.loads((ROOT / "config" / "pi" / "models.json").read_text())
    sandbox_models = json.loads((ROOT / "sandbox" / "pi" / "models.json").read_text())

    assert parser.getint("qwen3.5-4b", "c") == EXPECTED_CONTEXT
    assert set(pi_models["providers"]) == {"llama-cpp"}
    pi_contexts = {
        model["id"]: model["contextWindow"] for model in pi_models["providers"]["llama-cpp"]["models"]
    }
    assert pi_contexts == {"qwen3.5-4b": EXPECTED_CONTEXT, "minicpm5-2b": 32768}
    assert parser.getint("minicpm5-2b", "c") == 32768
    assert sandbox_models["providers"]["llama-cpp"]["models"][0]["contextWindow"] == EXPECTED_CONTEXT
    assert "anthropic" not in json.dumps(pi_models).lower()
    assert "gemini" not in json.dumps(pi_models).lower()


def test_sandbox_router_matches_local_inference_settings():
    compose = (ROOT / "sandbox" / "compose.yaml").read_text()

    assert '- "131072"' in compose
    assert "--cache-type-k" in compose
    assert "--cache-type-v" in compose
    assert "--n-gpu-layers" in compose
    assert "--reasoning-budget" in compose


def test_model_selector_has_no_automatic_cloud_route():
    selector = (ROOT / "pi" / "extensions" / "model-selector.ts").read_text()

    assert 'pi.on("turn_start"' not in selector
    assert "GEMINI_API_KEY" not in selector
    assert 'registerCommand("local-model"' in selector
    assert 'registerCommand("gemini-model"' not in selector


def test_disabled_prompt_extension_does_not_duplicate_telemetry():
    extension = (ROOT / "pi" / "extensions" / "agent-system-prompt.ts").read_text()

    assert "telemetry.jsonl" not in extension
    assert "tool_execution_start" not in extension
