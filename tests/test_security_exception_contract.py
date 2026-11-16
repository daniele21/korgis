from __future__ import annotations

from datetime import date
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_security_exceptions.py"
EXCEPTIONS = ROOT / ".engineering" / "security-exceptions.json"

spec = importlib.util.spec_from_file_location("verify_security_exceptions", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_repository_security_exception_contract_is_valid() -> None:
    payload = json.loads(EXCEPTIONS.read_text(encoding="utf-8"))
    assert module.validate(payload, today=date(2026, 8, 17)) == []


def test_exception_contract_rejects_expired_and_unowned_exception() -> None:
    payload = json.loads(EXCEPTIONS.read_text(encoding="utf-8"))
    payload["exceptions"][0]["expires_on"] = "2026-08-16"
    payload["exceptions"][0]["owner"] = ""

    errors = module.validate(payload, today=date(2026, 8, 17))

    assert any("expired on 2026-08-16" in error for error in errors)
    assert any("owner must be non-empty" in error for error in errors)


def test_exception_contract_rejects_duplicate_ids_and_missing_controls() -> None:
    payload = json.loads(EXCEPTIONS.read_text(encoding="utf-8"))
    duplicate = dict(payload["exceptions"][0])
    payload["exceptions"].append(duplicate)
    payload["exceptions"][0]["compensating_controls"] = []

    errors = module.validate(payload, today=date(2026, 8, 17))

    assert any("duplicate exception id" in error for error in errors)
    assert any("compensating_controls" in error for error in errors)


def test_product_and_consumer_sources_do_not_enable_diskcache() -> None:
    assert module.validate_no_llama_disk_cache_wiring(
        (
            ROOT / "src" / "local_llm_server",
            ROOT / "sdk" / "python" / "src",
            ROOT / "examples" / "korgis-consumer",
        )
    ) == []


def test_diskcache_guard_rejects_unsafe_wiring(tmp_path) -> None:
    source = tmp_path / "consumer.py"
    cases = {
        "import diskcache": "direct diskcache import",
        "from llama_cpp import LlamaDiskCache": "LlamaDiskCache-capable import",
        "from llama_cpp import *": "LlamaDiskCache-capable import",
        "LlamaDiskCache()": "LlamaDiskCache reference",
        "cache = llama_cpp.LlamaDiskCache()": "LlamaDiskCache access",
        "model.set_cache(cache)": "set_cache call",
        "llm = Llama(cache=cache)": "Llama(cache=...) configuration",
    }
    for statement, finding in cases.items():
        source.write_text(statement + "\n", encoding="utf-8")
        result = module.validate_no_llama_disk_cache_wiring((tmp_path,))
        assert any(finding in error for error in result), (statement, result)


def test_diskcache_guard_allows_unrelated_runtime_caches(tmp_path) -> None:
    (tmp_path / "allowed.py").write_text(
        'response_cache.trim()\n'
        'llm = Llama(model_path="model.gguf")\n'
        'kwargs = {"cache_type": "f16"}\n',
        encoding="utf-8",
    )
    assert module.validate_no_llama_disk_cache_wiring((tmp_path,)) == []
