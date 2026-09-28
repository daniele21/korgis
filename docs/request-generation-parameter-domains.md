# Request-generation parameter domains

Status: active
Document type: capability-reference
Owner: model/runtime capability metadata
Canonical scope: serving.request-generation-domains
Read when: declaring model-specific request tuning bounds or consuming model capability metadata from an external evaluation client
Last reviewed: 2026-09-03

Local LLM Server can expose explicitly declared, bounded **request-level generation parameter domains** through its existing model capability projection. These domains describe settings that an external evaluator may vary per inference request. They are capability metadata only: they do not mutate runtime-load configuration and they do not define an evaluation/search strategy.

## Source of truth

A model registry entry may declare `generation_parameter_domains`. Missing metadata means the domain is unavailable. Local LLM Server does not derive a range from backend request fields, configured defaults, filenames, or common industry conventions.

For example:

```yaml
models:
  example:
    model_id: org/example
    backend: llama_server
    modalities: [text]
    generation_parameter_domains:
      temperature:
        kind: float
        minimum: 0.0
        maximum: 0.8
        step: 0.1
      top_k:
        kind: integer
        minimum: 1
        maximum: 40
        step: 1
```

The public model projection serializes this as a stably sorted `generation_parameter_domains` array. Each item includes `provenance: registry_declared`. A model with no declarations exposes an empty array.

## Supported request-level domain names

The capability contract currently recognizes:

- `temperature`
- `top_p`
- `top_k`
- `min_p`
- `repeat_penalty`
- `presence_penalty`
- `frequency_penalty`
- `max_tokens`
- `enable_thinking`

Numeric domains require an explicit `kind`, `minimum` and `maximum`, with `minimum < maximum`. `step` is optional but, when present, must be positive and no larger than the declared span. Integer parameters require integer bounds and step values.

`enable_thinking` is a boolean domain. It must declare both `false` and `true`, and the model's **effective** thinking capability must be `switchable`. A fixed `none` or `always` runtime cannot advertise request-level thinking optimization.

## Deliberately excluded runtime-load settings

Runtime-load/lifecycle fields such as `ctx_size`, `n_threads`, `n_batch`, `n_ubatch`, GPU/offload settings and residency/concurrency controls are not request-generation domains. Declaring them under `generation_parameter_domains` fails registry validation.

Those settings may be observable through runtime identity/config capabilities, but changing them can require reload/restart or resource lifecycle transitions. They need a separate explicit lifecycle contract before an external evaluator may search them.

## Defaults are not domains

`default_temperature`, `default_top_p`, `default_top_k`, `default_min_p` and `default_repeat_penalty` in the resolved serving configuration are execution defaults. They do not imply any minimum, maximum or optimization support.

This distinction is intentional: a default answers “what happens when the request omits this field”; a declared domain answers “which bounded values does this concrete serving path explicitly allow an external evaluator to search”.

## Consumer contract

Consumers such as Performance Lab must:

1. search only explicitly returned domains;
2. treat an empty/missing domain as unavailable rather than inventing bounds;
3. preserve the declared model/runtime identity while evaluating candidates;
4. keep request-level tuning separate from runtime-load configuration;
5. own their own search strategy, benchmark execution and recommendation policy.

No mutation endpoint is introduced by this capability contract.
