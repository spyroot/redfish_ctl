# Live sanity-check scripts — safe reversible mutation round-trips

These scripts drive **real BMCs** to capture how each vendor responds to writes (OK / error / good-patch /
bad-patch), so the simulator is grounded in captured behavior — the golden workflows are *generated tests*,
never hand-coded simulator logic.

## 🚨 HARD SAFETY BLOCKER (applies to every operator and tool run — no exceptions)
**NEVER reboot, power-cycle, reset, factory-reset, flash firmware, delete a volume, erase a drive, or run ANY
host- or BMC-disrupting operation on a LIVE system without the operator's explicit per-target approval.**
These scripts are **read + reversible-config only**. Destructive actions are enumerated and NEVER invoked:
`ComputerSystem.Reset`, `Manager.Reset`, `*.ResetToDefaults`, `Chassis.Reset`, firmware update, `Volume`
create/delete, `Drive.SecureErase`, SecureBoot/RoT key ops, GPU power-cap/PowerSmoothing/WorkloadPower.
If in doubt, it is destructive — stop and ask.

## The contract every script MUST follow
1. **Only `redfish_ctl` touches the BMC. NEVER `curl` (or requests/raw HTTP).** If you need to READ a value or
   DO a write and `redfish_ctl` can't, that is a **GAP → implement the `redfish_ctl` command first** (a curl in a
   script is a bug, not a workaround).
2. **Round-trip + assert (no empty tests):** read current value **X** → set **Y** → **assert live == Y** → restore
   **X** → **assert live == X**. For BIOS (can't hot-revert): capture X, stage Y, assert pending==Y, stage X back,
   assert pending==X. Every assert compares a REAL value; a script that asserts nothing is rejected.
3. **Collect the trace → simulator.** Save the request + full response of every step (positive AND a negative:
   an invalid value that the BMC rejects) as a trace under `captures/<vendor>/<model>/<op>.{ok,err}.json`. If we
   never collected the ERROR / PATCH / POST response, that is a GAP. These traces become the mutation-rule /
   write-trace ground truth + the sim's negative-case responses.
4. **Target/creds via env only** (`REDFISH_IP`/`REDFISH_USERNAME`/`REDFISH_PASSWORD`) — never hardcode.
5. **Idempotent + self-cleaning:** if a step fails mid-way, the script restores X (best-effort) and exits non-zero.

## Layout
```
scripts/live_sanity_check/
  README.md                      (this contract)
  supermicro/gb300/<op>_roundtrip.sh
  supermicro/x10/<op>_roundtrip.sh
  hp/dl360/<op>_roundtrip.sh
  dell/xr8620t/<op>_roundtrip.sh   (Dell box decommissioned → validate against corpus, mark live-skip)
```

## MASTER SAFE-ACTION LIST (implement one by one; ✅=redfish_ctl cmd exists, 🔨=IMPLEMENT the missing cmd)
Standard Redfish URIs shown; the manager/system id is DISCOVERED (never hardcode `iDRAC.Embedded.1`).

### GB300 (supermicro/gb300 — NVIDIA HGX/OpenBMC, Managers/BMC_0)
| op | read | mutate | revert | status |
| --- | --- | --- | --- | --- |
| virtual-media mount/eject | `get_vm` ✅ | `insert_vm`/`eject_vm` 🔨 **Dell-hardcoded (`iDRAC.Embedded.1`→404) — generalize via discovery** | eject | GAP |
| ntp | `manager-network` ✅ | `ntp-set` ✅ (`--clear` restores an originally empty list) | restore list | script ready; live capture pending |
| identify LED (Chassis_0, System_0) | `identify-led` ✅ | `identify-led` ✅ (guarded PATCH) | set back | command ready; live trace pending |
| test-event (emit only) | — | `event-submit-test` ✅ | none | ✅ DONE (`event_submit_test.sh`, verified live) |
| event subscription create/delete | `event-service` ✅ (read) | 🔨 **new `subscription-create`/`subscription-delete`** | DELETE created id | GAP |
| **negative**: bad value on any above | — | (invalid enum/type) | n/a | capture error envelope |
NOTE: GB300 has **NO safe BIOS attribute** (all 360 writable attrs are hardware-critical) — do the BIOS round-trip on iLO/Dell, not GB300.

### iLO (hp/dl360 — Systems/1, Managers/1) — LIVE (WireGuard up)
| op | read | mutate | revert | status |
| --- | --- | --- | --- | --- |
| BIOS AdminName / AdminEmail / AdminPhone | `bios`/`attr` ✅ | `bios-change`/`attr-update` ✅ (verify iLO path `/systems/1/bios/settings/`) | stage X back | verify live |
| AssetTag (Chassis/1, Systems/1) | `chassis`/`system` ✅ | 🔨 **new `asset-tag-set` (or generic property PATCH)** | restore | GAP |
| identify LED (LocationIndicatorActive / IndicatorLED) | `identify-led` ✅ | `identify-led` ✅ (guarded PATCH) | set back | command ready; live trace pending |
| boot override (one-time, inert until reboot) | `current_boot` ✅ | `boot-one-shot` ✅ | set Disabled | verify live |
| **negative**: LocationIndicatorActive="Lit", BootTarget="Banana", unknown BIOS attr | — | invalid | n/a | capture error envelope |

### X10 (supermicro/x10 — user's own box, any op) — enumerate from `tests/supermicro_x10_corpus` + live
Safe set: identify LED, NTP, BIOS non-critical, virtual media (X10 uses Supermicro CfgCD → `vm-mount` ✅). Full
enumeration is task X10-ENUM.

### Dell (dell/xr8620t) — box DECOMMISSIONED/offline → scripts validate against the committed corpus + mark
`SKIP: no live Dell target`. Safe ops mirror iLO (AssetTag, LED, NTP, BIOS Admin*).

## Engine gaps to IMPLEMENT (prerequisites — one PR each, mutating = gated review)
1. **generalize `insert_vm`/`eject_vm`** — discover Manager id (kills the `iDRAC.Embedded.1` 404).
2. **`redfish_ctl get <uri>` / `raw`** — generic read so no script ever needs curl.
3. ✅ **`identify-led`** — PATCH LocationIndicatorActive/IndicatorLED on Chassis/System.
4. **`ntp-set --clear`** — ✅ command support + GB300 round-trip script ready; live capture pending.
5. **`subscription-create` / `subscription-delete`** — EventDestination lifecycle.
6. **`asset-tag-set`** (or a guarded generic property PATCH).
7. **trace capture** — a `--capture-trace <dir>` flag (or wrapper) that saves request+response per call → sim.
