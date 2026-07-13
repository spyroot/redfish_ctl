# supermicro_fixtures

Minimal Supermicro GB300 NVL Redfish fixtures, derived from a read-only capture of an approved lab
BMC (identifiers redacted: SerialNumber/UUID/AssetTag/PartNumber). Two-system / two-manager shape
(System_0 host + HGX_Baseboard_0 baseboard; BMC_0 + HGX_BMC_0). Used by the vendor-aware mock
(conftest.py `redfish_mock_factory`) to prove discovery resolves real non-Dell ids. Expand via the
fixture-capture SOP. Not Dell's `dell_fixtures/` overlay.
