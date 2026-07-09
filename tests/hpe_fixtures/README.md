# HPE iLO test fixtures (third-party, BSD-3-Clause)

A bounded, link-consistent slice of an **HPE ProLiant DL380a (iLO)** Redfish tree,
used to prove redfish_ctl's commands are vendor-neutral on a non-Dell, non-Supermicro
host. The mock (`tests/conftest.py`, via `redfish_mock_factory("hpe")`) serves these
by URL exactly like the Supermicro corpus.

## Provenance

Derived from the **HPE iLO Redfish Emulator** mockups:
<https://github.com/HewlettPackard/ilo-redfish-emulator> (`mockups/DL380a`).

Copyright 2022 Hewlett Packard Enterprise Development LP. Licensed **BSD-3-Clause**;
the full license is retained in `LICENSE.HPE` in this directory, as its terms
require for redistribution. This is a redistributed *slice* of that dataset, not the
whole tree.

## How it was produced

```
python tests/tools/import_redfish_mockup.py \
  --mockup <clone>/ilo-redfish-emulator/mockups/DL380a \
  --out tests/hpe_fixtures --max-members 4 --max-files 200
```

`import_redfish_mockup.py` converts a DMTF/HPE mockup (an `index.json` directory
tree) into the flat `_redfish_v1_..._.json` fixture names the mock expects. Re-run
it against any DMTF mockup (Dell, HPE, generic) to add another vendor overlay.
