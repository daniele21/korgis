# Embed Korgis: Python consumer preview

Status: experimental
Document type: developer guide
Owner: developer experience
Canonical scope: integration.python-consumer
Read when: building a local Python or macOS application on Korgis
Last reviewed: 2026-10-11

## Outcome and ownership

The separately installable package in sdk/python is a dependency-free, typed consumer of the *existing* Korgis HTTP product boundary. It helps a product bind application roles to exact model keys and call a running local runtime without duplicating backend, scheduler, resource or capability policy.

Korgis owns model lifecycle, admission, inference and runtime evidence. The application continues to own its data, prompts, structured domain schemas, validation, storage, UI, persistence and app-level jobs.

This is a narrow P0 developer integration slice, **not** implementation of the future canonical M8 ApplicationProfile, resource dry-run planner, M9 on-demand lifecycle or a production-grade native embedding library. The client bindings version is intentionally named korgis-client-bindings-v1 to avoid suggesting a stable server profile contract.

## Package and first integration

Install the SDK alone from the repository: python -m pip install ./sdk/python.

See examples/korgis-consumer/closedroom-bindings.json. The explicit role is analysis, configured to use the exact local registry key nemotron-nano-4b with task chat. No role is allowed to silently replace its model; schema versions, unknown fields and unsupported tasks fail before sending a request.

To attach to an already running Korgis daemon use the code in examples/korgis-consumer/closedroom_analysis.py. This attach mode verifies health and the exact resident model list. A connection never starts or shuts down somebody else's daemon.

To run a developer-owned server under a context manager:

    from korgis_sdk import Korgis
    with Korgis.managed("examples/korgis-consumer/closedroom-bindings.json") as ai:
        text = ai.role("analysis").generate("Summarize this meeting.")

This mode requires the existing Korgis local-llm CLI and its native dependencies to be installed separately. It starts only declared models using --no-download, waits for readiness, and terminates the process group that it started when the context exits. It does not install models, infer model compatibility from names, switch models, perform profile resource planning, or move data to the cloud.

The SDK supports chat/vision through /v1/chat/completions and first-class multipart transcription through /v1/audio/transcriptions. It does not offer streaming or asynchronous job persistence; transcription has a 32 MiB input guard in this first version. Structured generation is a chat request with the server's supported response_format, and remains application-owned for domain validation. There is no permissive JSON repair.

## Safety and limitations

- Only HTTP loopback endpoints are accepted in this version. Redirects are blocked to prevent implicit network disclosure.
- HTTP failure messages are intentionally sanitized so application error reporting does not repeat sensitive server bodies.
- The current server has no loopback HTTP authentication. Another local process may be able to access the same server. Consequently managed mode is a **developer preview**; do not ship it in a privacy-sensitive packaged application without strengthening local access control and authenticated process ownership.
- A dynamically selected loopback port is checked at launch, but the current CLI has no atomic inherited-listener handshake. This race must be removed before treating managed mode as a production embedding guarantee.
- The Korgis core remains authoritative for capability and resource admission. The client binding only fixes the declared role-to-model route; the server rejects unsupported requests.
- Pinned, warm and on_demand residency are not exposed through this binding format yet. Managed mode starts its declared models resident. New residency behavior belongs in M8-M9 and must preserve exact runtime identity and lease safety.
- Media processing and actual native model packages are not bundled into this Python-only SDK. Representative Apple Silicon packaging, cleanup and process tests remain required for end-user distribution.
- The app's existing process/service supervisor must remain the sole app-level lifecycle owner; the SDK must not be used to start a second Korgis process behind an already managed ClosedRoom sidecar.

## Reference consumers

ClosedRoom currently owns a LocalLLMSidecar under src/local_asr_server/runtime/llm_sidecar.py. It should initially consume the connect mode in its existing analysis provider while the current RuntimeServiceManager continues to own its service. A later refactoring can reuse generic process functionality only if equivalent lifecycle, identity and security behavior has been demonstrated.

RedactGuard Android currently consumes Harnex through Android Binder. Korgis should not replace that Android owner. The transferable idea is the explicit Consumer SDK/runtime responsibility split, not a shared daemon or native Android SDK within Korgis.

## Verification

Repository-local deterministic tests: python -m pytest tests/test_embedded_sdk.py -v.

Independent packaging smoke: python -m pip install ./sdk/python followed by importing korgis_sdk from an isolated working directory.

A broader end-to-end acceptance slice should exercise exact model routing, launch failure, concurrent app exit, orphan-process checks, cancellation, port collision, resource denial and representative Apple Silicon packaged-app behavior before claiming production-readiness.

Future sequencing remains the repository roadmap M8 (canonical profile/plan), M9 (on-demand lifecycle), M10 (workload scheduling) and M12 (stable integration contracts).
