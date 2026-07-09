"""iDRAC
Author Mus spyroot@gmail.com
"""
from abc import abstractmethod
from typing import Optional

from ..cmd_utils import find_ids
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult


class DrivesQuery(IDracManager,
                  scm_type=ApiRequestType.Drives,
                  name='drives_query',
                  metaclass=Singleton):
    """iDRACs REST API fetch storage information.
    """

    def __init__(self, *args, **kwargs):
        super(DrivesQuery, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Registers command args
        :param cls:
        :return:
        """
        cmd_parser = cls.base_parser()
        cmd_parser.add_argument('-c', '--controller', required=False, type=str,
                                default="",
                                help="controller name.")

        cmd_parser.add_argument('--filter', required=False,
                                type=str, dest="data_filter",
                                default="",
                                help="Filter Controllers/Drives/Volumes. "
                                     "(Example filter both Driver "
                                     "and Volumes -c AHCI.Embedded.2-1 "
                                     "--filter Drives,Volumes")

        help_text = "command fetch the storage drives information"
        return cmd_parser, "storage-drives", help_text

    def execute(self,
                controller: Optional[str] = None,
                data_filter: Optional[str] = None,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Get storage controller details.
        :param data_filter:
        :param do_expanded:
        :param controller: if empty cmd will return list of controllers.
        :param verbose: enables verbose output
        :param do_async: will not block and return result as future.
        :param filename: if filename indicate call will save a bios setting to a file.
        :param data_type:  json, xml etc.
        :return: named tuple CommandResult
        :raise: AuthenticationFailed, UnexpectedResponse
        """
        drives = self.sync_invoke(
            ApiRequestType.StorageViewQuery,
            "storage_get", controller=controller, data_filter="Drives"
        )

        odata_ids = find_ids(drives.data, "@odata.id")
        final_data = []
        disk_ids = {}

        raid_disk_ids = []
        none_raid_disk_ids = []
        for oids in odata_ids:
            disk_id = oids.split("/")[-1]
            disk_ids[disk_id] = oids
            cmd_rest = self.base_query(oids,
                                       filename=None,
                                       do_async=do_async,
                                       do_expanded=do_expanded)
            if 'Oem' in cmd_rest.data:
                oem = cmd_rest.data['Oem']
                if 'Dell' in oem:
                    raid_status = oem['Dell']['DellPhysicalDisk']['RaidStatus']
                    if 'NonRAID' in raid_status:
                        none_raid_disk_ids.append(disk_id)
                    else:
                        raid_disk_ids.append(disk_id)

            final_data.append(cmd_rest.data)

        final_data.append({"disk_ids": disk_ids})
        final_data.append({"none_raid_disk_ids": none_raid_disk_ids})
        final_data.append({"raid_disk_ids": raid_disk_ids})
        return CommandResult(final_data, None, None, None)
