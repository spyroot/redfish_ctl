# BIOS tuning profiles

Each `*.json` file in this directory is a named BIOS tuning profile — a small,
vendor-scoped set of BIOS attribute overrides that a `bios-profile` reader can
list, show, diff against a live system, and (guarded) stage via the existing
`bios-change` path.

## Schema

| Field         | Type   | Meaning                                                            |
| ------------- | ------ | ----------------------------------------------------------------- |
| `name`        | string | Profile id; must equal the filename stem.                         |
| `vendor`      | string | Vendor the profile targets (`supermicro`, `dell`, …).            |
| `model`       | string | Model/family label (e.g. `GB300`, `PowerEdge`).                  |
| `description` | string | One sentence on what the profile does and why.                   |
| `risk`        | string | `low` / `medium` / `high` — operator-facing caution level.       |
| `notes`       | string | Optional: apply semantics, provenance.                           |
| `attributes`  | object | `{ <BiosAttributeName>: <value> }` overrides to stage.           |

## No invented knobs

Every key in `attributes` must exist in the target vendor's captured BIOS
attribute registry, and its value must be allowable there. This is enforced by
`tests/test_bios_profiles_specs.py`, which validates each profile against the
committed registries:

- `supermicro` → the GB300 corpus registry
  (`_redfish_v1_Registries_BiosAttributeRegistry_BiosAttributeRegistry.json`, packed in
  `tests/supermicro_gb300_corpus.tar.gz`).
- `dell` → `tests/idrac_fixtures/_redfish_v1_Systems_System.Embedded.1_Bios_BiosRegistry.json`.

GB300 is a Grace-Blackwell (ARM) platform: its registry exposes knobs such as
`ServerPowerControl`, `ActiveCores`, and `EGM` — **not** the x86
`C-states`/`NPS`/`turbo` attributes. Do not add a knob that isn't in the
registry; add the matching registry capture first.
