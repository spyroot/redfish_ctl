# supermicro_x10_fixtures

Read-only Redfish discovery capture from a home-lab **Supermicro X10SDV-TLN4F**
(ASPEED AST2400 BMC, firmware 4.00, **Redfish 1.0.1**), committed as a test corpus.

This is the "thin firmware" counterpart to the fuller GB300 `supermicro_fixtures/`:
the X10 Redfish is frozen at 1.0.1 and does **not** expose `UpdateService`,
`FirmwareInventory`, a `Bios` resource, or standard VirtualMedia — useful for
testing vendor-neutral commands against a minimal real-world Redfish tree.

Served via `redfish_mock_factory("supermicro_x10")` (see `tests/conftest.py`).
Captured with `redfish_ctl discovery`. Contains lab identifiers (MACs/IP/account
names), no credentials or tokens — committed with owner approval, same as the
GB300 corpus.
