# Authoring rules

## One rule, one semantic outcome

Each rule should answer one observable question. Split rules when any of these
change independently:

- actor direction;
- HTTP method or operation kind;
- target relation;
- request/resource state;
- exact status or accepted class;
- headers/body requirements;
- state transition;
- normative strength.

## Required source metadata

Every canonical DMTF rule must include:

```yaml
source:
  document: DSP0266
  version: 1.24.0
  sections: ["12.1.2"]
  statement: event-subscription-create-success
  wording: shall
```

`statement` is a stable local identifier, not copied specification prose.

## Status selectors

Use one of these forms:

```yaml
status:
  accept: {matcher: exact, values: [201]}
  emit: 201
```

```yaml
status:
  accept: {matcher: one_of, values: [200, 202, 204]}
  emitPreferred: 204
```

```yaml
status:
  accept: {matcher: class, values: [2xx]}
  emitPreferred: 204
  examples: [200, 204]
  examplesExhaustive: false
```

```yaml
status:
  accept: {matcher: derived, requiredClass: 4xx}
  resolver: global_status_by_failure_cause
```

Do not turn examples introduced by wording such as “such as” into an exhaustive
list.

## Inheritance

A service clause may narrow or add effects to generic method behavior. Record
this explicitly:

```yaml
inherits:
  - method.delete.success
```

The service-specific rule wins when it fixes an exact result. Inherited rules
remain visible in the generated matrix so the origin of each alternative is
traceable.

## OEM overlays

OEM observations live under
`contracts/oem/<vendor>/<product>/<firmware>.yaml`. They must not copy a base
rule into a modified canonical rule. Use:

```yaml
classification: known_dmtf_deviation
behavior:
  strictDmtfMode: {accept: false, emit: false}
  oemCompatibilityMode: {accept: true, emit: false, severity: warning}
```

A live observation needs product family, firmware version, fixture evidence,
and ideally a reproducible request/response transcript with secrets removed.
