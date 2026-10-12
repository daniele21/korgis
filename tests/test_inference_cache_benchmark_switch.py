"""Deterministic inference cache can be disabled for cold benchmark repetitions."""
from local_llm_server.server import ServerSettings, create_app


def test_benchmark_cache_flag_disables_response_cache():
    app = create_app(settings=ServerSettings(inference_cache_max_entries=0))
    assert app.state.settings.inference_cache_max_entries == 0
    assert not app.state.inference_response_cache.enabled


def test_normal_server_preserves_cache_by_default():
    app = create_app()
    assert app.state.inference_response_cache.enabled
    assert app.state.settings.inference_cache_max_entries == 256


def test_negative_cache_max_entries_is_also_disabled():
    app = create_app(settings=ServerSettings(inference_cache_max_entries=-1))
    assert not app.state.inference_response_cache.enabled
