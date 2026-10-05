from local_llm_server.runtime_identity import resolved_config_digest, resolved_config_payload


def test_public_runtime_config_payload_matches_digest_boundary() -> None:
    config = {
        "backend": "llama_cpp",
        "ctx_size": 4096,
        "n_threads": 8,
        "llama_server_min_build": 10828,
        "model_path": "/private/model.gguf",
        "download_url": "https://private.example/model",
    }

    payload = resolved_config_payload(config)

    assert payload == {
        "backend": "llama_cpp",
        "ctx_size": 4096,
        "n_threads": 8,
        "llama_server_min_build": 10828,
    }
    assert len(resolved_config_digest(config)) == 64
    assert "model_path" not in payload
    assert "download_url" not in payload
