# External docs (public-facing)

Public, value-free documentation for redfish_ctl users and contributors: purpose, install, configuration,
read-only and mutating workflows (with safety implications), async vs sync usage, troubleshooting, and
safety rules. No internal addresses, credentials, capacities, or unreleased plans — those live in
`docs/internal/` (gitignored).

Canonical public pages elsewhere in `docs/` (commands, observability, telemetry, secrets, vendors,
simulation) remain the reference; this directory holds external-facing material that is explicitly
curated for publication. When promoting an internal doc here, strip every lab specific first (neutral
"Redfish endpoint", not a lab IP; env-var names, not values).
