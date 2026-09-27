"""
cli.py — entry point for the `local-llm` command.

Subcommands:
  local-llm serve                — start the HTTP server
  local-llm models               — list available models
  local-llm download             — download a model without starting the server
  local-llm verify-artifact      — explicitly hash and cache one local model artifact
  local-llm evidence-reclamation — run isolated repeated lifecycle evidence
  local-llm evidence-review      — review compatible repeated hardware reports
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Mapping


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="local-llm",
        description="Self-contained local LLM server with OpenAI-compatible API.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    p_serve = sub.add_parser("serve", help="Start the LLM server.")
    p_serve.add_argument(
        "--backend",
        choices=[
            "llama_cpp",
            "mlx",
            "llama_server",
            "mlx_vlm_server",
            "diffusers_image",
            "mflux_image",
        ],
        default=None,
        help="Inference backend: llama_cpp/llama_server for GGUF, mlx for text MLX, mlx_vlm_server for MLX vision, diffusers_image for generic image generation, mflux_image for MLX image generation.",
    )
    p_serve.add_argument(
        "--model",
        default=None,
        help="Registry key (e.g. qwen3-8b). Default: registry default_model.",
    )
    p_serve.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="Registry keys to keep resident simultaneously.",
    )
    p_serve.add_argument(
        "--default-model",
        default=None,
        dest="default_model",
        help="Default route when a request omits the model field.",
    )
    p_serve.add_argument(
        "--model-path",
        default=None,
        dest="model_path",
        help="Direct model path/ref. GGUF uses a file; MLX/Diffusers/MFlux use a local model directory or Hugging Face repo.",
    )
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--ctx-size", type=int, default=None, dest="ctx_size")
    p_serve.add_argument(
        "--max-kv-size", type=int, default=None, dest="max_kv_size",
        help="Maximum MLX KV-cache size in tokens.",
    )
    p_serve.add_argument("--n-gpu-layers", type=int, default=None, dest="n_gpu_layers")
    p_serve.add_argument("--n-threads", type=int, default=None, dest="n_threads")
    p_serve.add_argument("--llama-server-port", type=int, default=None, dest="llama_server_port")
    p_serve.add_argument("--llama-server-bin", default=None, dest="llama_server_bin")
    p_serve.add_argument("--mlx-vlm-server-port", type=int, default=None, dest="mlx_vlm_server_port")
    p_serve.add_argument("--mmproj-path", default=None, dest="mmproj_path")
    p_serve.add_argument("--image-device", choices=["auto", "mps", "cuda", "cpu"], default=None, dest="image_device")
    p_serve.add_argument("--image-dtype", choices=["bfloat16", "float16", "float32"], default=None, dest="image_dtype")
    p_serve.add_argument("--image-width", type=int, default=None, dest="image_width")
    p_serve.add_argument("--image-height", type=int, default=None, dest="image_height")
    p_serve.add_argument("--image-num-inference-steps", type=int, default=None, dest="image_num_inference_steps")
    p_serve.add_argument("--image-max-inference-steps", type=int, default=None, dest="image_max_inference_steps")
    p_serve.add_argument("--image-max-pixels", type=int, default=None, dest="image_max_pixels")
    p_serve.add_argument("--image-guidance-scale", type=float, default=None, dest="image_guidance_scale")
    p_serve.add_argument("--image-output-format", choices=["png", "jpeg", "jpg", "webp"], default=None, dest="image_output_format")
    p_serve.add_argument("--startup-timeout", type=int, default=None, dest="startup_timeout")
    p_serve.add_argument("--max-concurrent-requests", type=int, default=None, dest="max_concurrent_requests")
    p_serve.add_argument("--chat-format", default=None, dest="chat_format")
    p_serve.add_argument("--force-json", action=argparse.BooleanOptionalAction, default=None)
    p_serve.add_argument("--show-thinking", action=argparse.BooleanOptionalAction, default=None, dest="show_thinking")
    p_serve.add_argument("--enable-thinking", action=argparse.BooleanOptionalAction, default=None, dest="enable_thinking")
    p_serve.add_argument("--no-download", action="store_true", default=False, dest="no_download",
                         help="Fail if the model is not already downloaded.")
    p_serve.add_argument("--verbose", action="store_true", default=False)
    p_serve.add_argument(
        "--enable-admin-api",
        action="store_true",
        default=False,
        help="Enable control-plane model/resource/evidence/evaluation endpoints.",
    )
    p_serve.add_argument(
        "--cors-origin",
        action="append",
        default=[],
        help="Allowed browser origin; repeat for multiple origins. CORS is disabled by default.",
    )

    sub.add_parser("models", help="List available models from the registry.")

    p_download = sub.add_parser("download", help="Download a model without starting the server.")
    p_download.add_argument("model", help="Registry key (e.g. qwen3-8b).")

    p_verify = sub.add_parser(
        "verify-artifact",
        help="Explicitly SHA-256 verify one resolved local single-file model artifact.",
    )
    p_verify.add_argument("model", help="Registry key to verify.")
    p_verify.add_argument(
        "--model-path",
        default=None,
        dest="model_path",
        help="Optional explicit local artifact path for this verification.",
    )

    p_evidence = sub.add_parser(
        "evidence-reclamation",
        help="Run repeated isolated worker load/infer/stop cycles and write a JSON evidence report.",
    )
    p_evidence.add_argument("--model", required=True, help="Registry model key to exercise.")
    p_evidence.add_argument("--model-path", default=None, dest="model_path")
    p_evidence.add_argument(
        "--backend",
        choices=["llama_cpp", "mlx", "llama_server", "mlx_vlm_server"],
        default=None,
    )
    p_evidence.add_argument(
        "--backend-version",
        default=None,
        dest="backend_version",
        help="Explicit backend/binary version when it cannot be resolved automatically.",
    )
    p_evidence.add_argument(
        "--accelerator",
        default=None,
        help="Optional evidence label for the active accelerator/device class.",
    )
    p_evidence.add_argument("--cycles", type=int, default=3)
    p_evidence.add_argument("--max-tokens", type=int, default=32, dest="max_tokens")
    p_evidence.add_argument(
        "--prompt",
        default="Reply with the single word OK.",
        help="Local workload prompt. Prompt/output are not written to the evidence report.",
    )
    p_evidence.add_argument(
        "--settle-seconds",
        type=float,
        default=2.0,
        dest="settle_seconds",
        help="Delay after each worker stop before the after-stop resource snapshot.",
    )
    p_evidence.add_argument(
        "--output",
        required=True,
        help="Destination JSON report path.",
    )
    p_evidence.add_argument(
        "--no-download",
        action="store_true",
        default=False,
        dest="no_download",
        help="Fail if required model artifacts are not already available locally.",
    )

    p_review = sub.add_parser(
        "evidence-review",
        help="Review repeated compatible hardware evidence reports without producing an auto-eviction recommendation.",
    )
    p_review.add_argument(
        "reports",
        nargs="+",
        help="Two or more worker reclamation JSON reports by default.",
    )
    p_review.add_argument("--min-reports", type=int, default=2, dest="min_reports")
    p_review.add_argument(
        "--min-complete-cycles",
        type=int,
        default=6,
        dest="min_complete_cycles",
    )
    p_review.add_argument(
        "--allow-exploratory-identity",
        action="store_true",
        default=False,
        dest="allow_exploratory_identity",
        help="Permit exploratory identity for descriptive review only.",
    )
    p_review.add_argument(
        "--allow-error-cycles",
        action="store_true",
        default=False,
        dest="allow_error_cycles",
        help="Do not make lifecycle error cycles an insufficiency gate for exploratory analysis.",
    )
    p_review.add_argument(
        "--output",
        default=None,
        help="Optional destination JSON. Without it the review is printed to stdout.",
    )

    args = parser.parse_args()

    if args.command == "serve":
        _cmd_serve(args)
    elif args.command == "models":
        _cmd_models()
    elif args.command == "download":
        _cmd_download(args.model)
    elif args.command == "verify-artifact":
        _cmd_verify_artifact(args.model, model_path=args.model_path)
    elif args.command == "evidence-reclamation":
        _cmd_evidence_reclamation(args)
    elif args.command == "evidence-review":
        _cmd_evidence_review(args)


def _cmd_serve(args: argparse.Namespace) -> None:
    from .config import build_config
    from .policy_server import browser_base_url, run_server
    from .product_runtime import bootstrap_product_runtimes

    explicit: dict = {}
    for key in (
        "backend", "host", "port", "ctx_size", "max_kv_size", "n_gpu_layers", "n_threads",
        "llama_server_port", "llama_server_bin", "mlx_vlm_server_port", "mmproj_path",
        "image_device", "image_dtype", "image_width", "image_height",
        "image_num_inference_steps", "image_max_inference_steps", "image_max_pixels",
        "image_guidance_scale", "image_output_format",
        "startup_timeout", "max_concurrent_requests", "chat_format", "force_json", "show_thinking", "enable_thinking",
        "no_download", "verbose",
    ):
        val = getattr(args, key, None)
        if val is not None:
            explicit[key] = val

    selected_model = (
        args.default_model
        or args.model
        or (args.models[0] if args.models else None)
    )
    preview_cfg = build_config(
        model=selected_model,
        model_path=args.model_path,
        **explicit,
    )
    ui_url = browser_base_url(preview_cfg["host"], preview_cfg["port"])
    print(f"\n[*] Web UI configured at: {ui_url}", flush=True)
    print(
        "[*] Loading startup model; the HTTP server is not listening yet.",
        flush=True,
    )

    bootstrap = bootstrap_product_runtimes(
        model=args.model,
        model_path=args.model_path,
        models=args.models,
        default_model=args.default_model,
        explicit=explicit,
    )

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    run_server(
        bootstrap.cfg,
        bootstrap.engine,
        manager=bootstrap.manager,
        enable_admin_api=args.enable_admin_api,
        cors_origins=args.cors_origin,
        resource_policy_settings=bootstrap.resource_policy,
    )


def _cmd_models() -> None:
    from . import list_models
    from .registry import load_registry

    registry = load_registry()
    models_dir = registry["models_dir"]
    default = registry["default_model"]
    models = list_models()

    if not models:
        print("No models found in registry.")
        return

    print(f"\nAvailable models  (dir: {models_dir})\n")
    col_key = max(len(entry["key"]) for entry in models) + 2
    col_id = max(len(entry["model_id"]) for entry in models) + 2

    for entry in models:
        key = entry["key"]
        model_id = entry["model_id"]
        size = f"{entry['size_gb']:.1f} GB" if entry.get("size_gb") else "? GB"
        tags = ", ".join(entry.get("tags") or [])
        model_ready = bool(entry["downloaded"])
        status_text = "\033[92m✅ downloaded\033[0m" if model_ready else "\033[90m❌ not downloaded\033[0m"
        marker = " (default)" if key == default else ""
        backend = entry.get("backend", "llama_cpp")
        print(f"  {key:<{col_key}} {model_id:<{col_id}} {size:<8}  {backend:<13} [{tags}]  {status_text}{marker}")

    print()


def _cmd_download(model: str) -> None:
    from . import download_model

    try:
        download_model(model)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Model '{model}' is available locally.")


def _cmd_verify_artifact(model: str, *, model_path: str | None = None) -> None:
    from .artifact_verification import public_verification_summary, verify_model_artifact

    try:
        receipt = verify_model_artifact(model, model_path=model_path)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"Artifact verification failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(public_verification_summary(receipt), indent=2, sort_keys=True))


def _cmd_evidence_reclamation(args: argparse.Namespace) -> None:
    from .hardware_evidence import (
        HardwareEvidenceOptions,
        execute_hardware_reclamation_evidence,
        write_evidence_report,
    )

    options = HardwareEvidenceOptions(
        model=args.model,
        cycles=args.cycles,
        prompt=args.prompt,
        max_tokens=args.max_tokens,
        model_path=args.model_path,
        backend=args.backend,
        backend_version=args.backend_version,
        accelerator=args.accelerator,
        settle_seconds=args.settle_seconds,
        no_download=args.no_download,
    )
    output = Path(args.output)
    try:
        report = execute_hardware_reclamation_evidence(options)
        write_evidence_report(output, report)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Evidence run failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Evidence report written to {output.expanduser().resolve()}")


def _cmd_evidence_review(args: argparse.Namespace) -> None:
    from .hardware_evidence import write_evidence_report
    from .hardware_evidence_review import EvidenceReviewSettings, review_hardware_evidence

    try:
        reports = _load_evidence_reports([Path(value) for value in args.reports])
        review = review_hardware_evidence(
            reports,
            settings=EvidenceReviewSettings(
                min_reports=args.min_reports,
                min_complete_cycles=args.min_complete_cycles,
                require_verified_identity=not args.allow_exploratory_identity,
                require_zero_error_cycles=not args.allow_error_cycles,
            ),
        ).to_public_dict()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Evidence review failed: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        output = Path(args.output)
        write_evidence_report(output, review)
        print(f"Evidence review written to {output.expanduser().resolve()}")
    else:
        print(json.dumps(review, indent=2, sort_keys=True, ensure_ascii=False))


def _load_evidence_reports(paths: list[Path]) -> list[Mapping[str, Any]]:
    reports: list[Mapping[str, Any]] = []
    for path in paths:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"evidence report must contain a JSON object: {path}")
        reports.append(payload)
    return reports
