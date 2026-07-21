# Output Adapter Plan

The CLI should expose one kubectl-style output adapter that renders normalized
command results. It should not maintain separate ad-hoc JSON, YAML, and human
paths for Redfish errors or telemetry.

## Current Flags

Current global output-related flags include:

| Flag | Current meaning |
| --- | --- |
| `--json` | Enable JSON-oriented output. Currently defaults on in parser setup. |
| `--yaml` | Render output as YAML. |
| `--json_only` | Suppress extra human text around JSON. |
| `-d`, `--data_only` | Print only data payload where supported. |
| `--no-stdout`, `--no_stdout` | Suppress stdout rendering. |
| `--nocolor` | Disable terminal color. |
| `-f`, `--filename` | Write output to a file for commands that support it. |
| `--no_extra` | Suppress extra command metadata where supported. |
| `--no_action` | Avoid action execution for commands that support planning. |

Telemetry exporter subcommands also have `--output prometheus|signalfx|otlp`.
That flag selects an exporter backend and should remain subcommand-local.

## Target Adapter

Add a global renderer option:

```text
-o, --output human|json|yaml|name|wide
```

Expected modes:

| Mode | Contract |
| --- | --- |
| `human` | Operator-friendly text rendered from the normalized result. |
| `json` | Stable machine-readable JSON for the normalized result. |
| `yaml` | Stable machine-readable YAML for the same normalized result. |
| `name` | Stable object identity only, where the command naturally returns named resources. |
| `wide` | Human table with extra columns where command output is tabular. |

Add convenience aliases:

| Alias | Contract |
| --- | --- |
| `--machine` | Equivalent to `--output json --json_only --nocolor`. |
| `--raw` | Payload passthrough for commands that explicitly support raw Redfish response output. |

Legacy compatibility:

- keep `--json` as an alias for `--output json` during transition;
- keep `--yaml` as an alias for `--output yaml`;
- keep `--json_only` as a JSON formatting modifier until callers migrate;
- reject conflicting renderer flags with a usage error.

## Rendering Rules

- Renderer input is a normalized command result object.
- JSON and YAML never call `str(exception)`.
- Human rendering may call safe string helpers, but it cannot be the source of
  machine data.
- `@Message.ExtendedInfo`, schema pointers, registry pointers, HTTP status,
  target URI, and raw body fields survive JSON/YAML output.
- Log redirection stays separate: logs go to stderr or `--log-file`; machine
  results go to stdout unless `--no-stdout` is selected.
- `--insecure` only affects TLS verification and warning behavior. It must not
  change output shape.

## Gates

The transition needs these tests:

- parser rejects conflicting output flags;
- `--machine` expands to JSON-only, colorless output;
- JSON/YAML serialize identical normalized data;
- human output can include safe strings without losing machine fields;
- `--no-stdout` suppresses result output but not logging redirection;
- `--log-file` captures logs without contaminating JSON/YAML stdout;
- async command routes render non-2xx Redfish errors through the same adapter;
- exporter backend `--output` remains scoped to telemetry export.
