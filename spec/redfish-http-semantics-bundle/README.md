# Redfish HTTP Semantics Bundle

A machine-readable starter bundle for modelling Redfish HTTP behavior as an
atomic rule graph and generating flat status-code matrices from it.

The pinned DMTF authority, imported rule sets, generated artifacts, and rule
precedence are defined in
`contracts/dmtf/dsp0266/1.24.0/manifest.yaml`.

## Implemented scope

This bundle contains:

1. The complete DSP0266 Table 14 Redfish HTTP status catalog, paraphrased into
   machine-readable constraints.
2. Generic successful modification semantics for POST-create, PATCH, PUT,
   DELETE, and asynchronous task monitoring.
3. A detailed rule set for **DSP0266 section 12.1.2, POST to subscription
   collection**, including:
   - `201 Created` plus `Location` for completed subscription creation;
   - optional `EventDestination` representation;
   - inherited asynchronous `202 Accepted` behavior;
   - exact `400 Bad Request` for unsupported subscription parameters;
   - conflict/error resolution;
   - event-delivery acknowledgement as any successful `2xx`, with `204` as the
     deterministic simulator default;
   - subscription persistence, termination, payload splitting, no historical
     replay, and post-termination `404` behavior;
   - generic DELETE success alternatives inherited for unsubscribe.
4. A separate OEM compatibility overlay format that cannot rewrite the DMTF
   base contract.
5. A validator/generator, generated JSON/CSV/Markdown matrices, and offline
   contract tests.

This is **not yet a transcription of every normative statement in all of
DSP0266**. Event-subscription coverage is recorded in
`contracts/dmtf/dsp0266/1.24.0/coverage/eventing.yaml`. Add other service
clauses as separate rule-set files and coverage ledgers using the same pattern.

## Semantic key

Response identity includes the actors, operation, target, resource/request
state, HTTP status, and Redfish message identity. See `docs/SEMANTIC_KEY.md` for
the canonical fields and examples.

## Project integration

The `unit.all` gate, defined in `gates/manifest.yaml` and run by
`scripts/gates/unit/all.sh`, collects
`spec/redfish-http-semantics-bundle/tests/test_contracts.py` through the root
`pyproject.toml`. Run it only through the repository's standard CI entrypoint;
a standalone CI or virtual-environment path is intentionally not defined here.

The following read-only classifier command uses the project conda environment:

```bash
conda run -n redfish_ctl python \
  spec/redfish-http-semantics-bundle/tools/contract_tool.py classify \
  spec/redfish-http-semantics-bundle/examples/observations/subscription-create-201.yaml
```

Maintainers can regenerate the flattened matrices after editing a rule:

```bash
conda run -n redfish_ctl python \
  spec/redfish-http-semantics-bundle/tools/contract_tool.py generate
```

Refresh `SHA256SUMS` after any generated artifact changes.

## Legal note

The bundle contains original structure and paraphrased semantic summaries. It
does not redistribute the DMTF specification, schema bundle, or registry
bundle. DMTF document identifiers and section anchors are included for
attribution and traceability. Consult the pinned official documents for the
normative text.
