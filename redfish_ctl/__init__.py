# ruff: noqa: F403, I001
# from bios import BiosQuery
# from hardware import HardwareInventorQuery
from .redfish_shared import *

from .system.cmd_system import *
from .system.cmd_system_config import *
from .system.cmd_system_import import *
from .system.cmd_dell_system_lcd_errors import *
#
from .cmd_boot import *
from .dell_lc.cmd_dell_lc_api import *
from .dell_lc.cmd_dell_lc_export import *
from .dell_lc.cmd_dell_lc_log_comment import *
from .dell_lc.cmd_dell_lc_autodiscovery import *
from .dell_lc.cmd_dell_lc_rs import *
from .dell_lc.cmd_dell_lc_services import *
from .dell_lc.cmd_dell_lc_network_share_test import *
from .dell_lc.cmd_dell_lc_supportassist_status import *
from .metrics.cmd_dell_metric_actions import *
from .dell_lc.cmd_dell_lc_os_health_update import *
from .dell_lc.cmd_dell_lc_ism_installer import *
from .dell_lc.cmd_dell_lc_supportassist_export import *
from .dell_lc.cmd_dell_lc_supportassist_schedule import *
from .dell_lc.cmd_dell_lc_clear_provisioning import *
#
# compute
from .compute.cmd_power_state import *
from .compute.cmd_compute_setting import *

from .redfish_manager_shared import *
from .raid.cmd_raid_service import *
from .raid.cmd_dell_raid_blink import *
from .raid.cmd_dell_raid_spare import *
from .raid.cmd_dell_raid_cancel import *
from .raid.cmd_raid_patrol_read import *
from .raid.cmd_dell_raid_config_actions import *
from .raid.cmd_dell_raid_clear_actions import *
from .raid.cmd_dell_raid_rename_vd import *
from .raid.cmd_pd_state import *
from .raid.cmd_dell_raid_foreign_config import *
#
# bios commands
from .bios.cmd_bios import *
from .bios.cmd_bios_clear_pending import *
from .bios.cmd_bios_pending import *
from .bios.cmd_change_boot_order import *
from .bios.bios_registry import *
from .bios.cmd_bios_profile import *
from .bios.cmd_change_bios import *
from .bios.cmd_bios_snapshot import *
from .bios.cmd_bios_reset_default import *
from .bios.cmd_dell_bios_device_recovery import *
#
from .attribute.cmd_attribute import *
from .attribute.cmd_attribute_clear_pending import *
from .attribute.cmd_attribute_update import *
#
#
# # jobs command
from .jobs.cmd_jobs import *
from .jobs.cmd_job_get import *
from .jobs.cmd_job_services import *
from .jobs.cmd_job_watch import *
from .jobs.cmd_job_del import *
from .jobs.cmd_job_dell_services import *
from .jobs.cmd_job_delete_all import *
from .jobs.cmd_job_apply import *
from .jobs.cmd_job_queue_setup import *
#
# # firmwares cmds
from .firmware.cmd_firmware import *
from .firmware.cmd_firmware_inv import *
from .firmware.cmd_update_service import *
from .firmware.cmd_update_start import *

from .pci.cmd_pci import *

# manager cmds
from .manager.cmd_manager import *
from .manager.cmd_manager_network import *
from .manager.cmd_ntp_set import *
from .manager.cmd_manager_reset import *


# virtual medial cmds
from .virtual_media.cmd_virtual_media_get import *
from .virtual_media.cmd_virtual_media_insert import *
from .virtual_media.cmd_virtual_media_eject import *
from .virtual_media.cmd_smc_virtual_media import *
from .cmd_current_boot import *

from .cmd_query import *
from .cmd_get import *
from .cmd_wait import *

# storage
from .storage.cmd_storage_controllers import *
from .storage.cmd_storage_list import *
from .storage.cmd_storage_get import *
from .storage.cmd_drives import *
from .storage.cmd_drive_secure_erase import *
from .storage.cmd_convert_none_raid import *
from .storage.cmd_convert_to_raid import *

# chassis cmd
from .chassis.cmd_chassis_query import *
from .chassis.cmd_chasis_reset import *
from .chassis.cmd_asset_tag_set import *
from .chassis.cmd_identify_led import *
from .sensors.cmd_sensors import *
from .controls.cmd_controls import *
from .controls.cmd_control_reset_defaults import *
from .thermal.cmd_thermal import *
from .power.cmd_power import *
from .power.cmd_power_smoothing import *
from .power.cmd_workload_power import *
from .thermal.cmd_leak_detectors import *
from .environment.cmd_environment_metrics import *
from .metrics.cmd_processor_metrics import *
from .metrics.cmd_memory_metrics import *
from .metrics.cmd_gpu_metrics import *
from .telemetry.cmd_metric_reports import *
from .telemetry.cmd_metric_definitions import *
from .telemetry.cmd_telemetry_clear_reports import *
from .telemetry.cmd_telemetry_reset_definitions import *
from .telemetry.cmd_telemetry_submit_test import *
from .telemetry.cmd_exporter import *
from .component_integrity.cmd_component_integrity import *
from .component_integrity.cmd_spdm_measurements import *
from .network.cmd_network_adapters import *
from .network.cmd_network_adapter_reset import *
from .network.cmd_dell_network_attributes import *
from .network.cmd_nic_firmware import *
from .ports.cmd_nvlink_ports import *
from .actions.cmd_action_list import *
from .events.cmd_event_service import *
from .events.cmd_event_submit_test import *
from .events.cmd_subscription_lifecycle import *
from .compute.cmd_system_reset import *
from .logs.cmd_logs import *
from .logs.cmd_log_clear import *
from .logs.cmd_log_collect_diag import *
from .licenses.cmd_license_install import *
from .licenses.cmd_dell_license_actions import *
from .network.cmd_ethernet_interfaces import *
from .security.cmd_secure_boot import *
from .security.cmd_certificates import *
from .security.cmd_certificate_generate_csr import *
from .firmware.cmd_firmware_update import *
from .telemetry.cmd_telemetry_triggers import *
from .network.cmd_network_ports import *
from .network.cmd_dell_switch_port_refresh import *
from .oem.cmd_oem_info import *
from .oem.cmd_dell_card_sekm_test import *
from .oem.cmd_dell_vflash_state import *
from .oem.cmd_hpe_test_actions import *
from .oem.cmd_hpe_kerberos_keytab import *
from .oem.cmd_dell_card_test_actions import *
from .oem.cmd_nvidia_debug_token import *
from .oem.cmd_smc_node_manager import *
from .oem.cmd_hpe_chassis_actions import *
from .oem.cmd_dell_card_cert_export import *
from .oem.cmd_dell_card_csr import *
from .oem.cmd_dell_card_hw_proof import *
from .oem.cmd_dell_card_key_management import *
from .oem.cmd_dell_persistent_initialize_media import *
from .oem.cmd_dell_persistent_partition import *
from .oem.cmd_dell_card_group_actions import *
from .manager.cmd_console_info import *
from .serial_console.cmd_serial_console import *
from .manager.cmd_manager_time import *
from .chassis.cmd_chassis_query import *

# dell oem attach
from .delloem.delloem_attach_status import *
from .delloem.delloem_actions import *
from .delloem.delloem_attach import *
from .delloem.delloem_detach import *
from .delloem.delloem_disconnect import *
from .delloem.delloem_get_networkios import *
from .delloem.delloem_boot_netios import *
from .delloem.delloem_os_deployment import *
from .delloem.cmd_dell_software_update_schedule import *


# tasks
from .tasks.cmd_tasks_list import *
from .tasks.cmd_tasks_get import *
from .tasks.cmd_task_watch import *

from .volumes.cmd_initilize import *
from .volumes.cmd_volumes import *
from .volumes.cmd_virtual_disk import *
from .volumes.cmd_volume_manage import *

# boot options
from .boot_options.cmd_boot_option_list import *
from .boot_options.cmd_boot_options_query import *


# boot sources
from .boot_source.cmd_boot_one_shot import *
from .boot_source.cmd_boot_settings import *
from .boot_source.cmd_boot_source_get import *
from .boot_source.cmd_clear_pending import *
from .boot_source.cmd_pending import *
from .boot_source.cmd_enable import *
from .boot_source.cmd_update import *
from .boot_source.cmd_boot_source_registry import *
from .boot_source.cmd_boot_state import *

# account
from .accounts.cmd_accounts import *
from .accounts.cmd_account_manage import *
from .accounts.cmd_account_sshkey import *
from .accounts.cmd_query_account import *
from .accounts.cmd_account_svc import *
from .accounts.cmd_privilage_registry import *

from .discovery.cmd_discovery import *
from .discovery.cmd_bmc_scan import *
from .fleet.cmd_fleet import *
from .vendors.cmd_capability_report import *
