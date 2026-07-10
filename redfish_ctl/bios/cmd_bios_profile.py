"""Read the committed BIOS tuning profile catalog."""

import json
from abc import abstractmethod
from pathlib import Path
from typing import Optional

from ..cmd_utils import save_if_needed
from ..idrac_manager import IDracManager
from ..idrac_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult

PROFILE_DIR = Path(__file__).resolve().parents[2] / "specs" / "profiles"
SUMMARY_KEYS = ("name", "vendor", "model", "description", "risk")


class BiosProfile(IDracManager,
                  scm_type=ApiRequestType.BiosProfile,
                  name="bios-profile",
                  metaclass=Singleton):
    """Read local BIOS profile specifications without contacting a BMC."""

    def __init__(self, *args, **kwargs):
        super(BiosProfile, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the read-only ``bios-profile`` catalog subcommand."""
        cmd_parser = cls.base_parser(is_async=False, is_expanded=False)
        cmd_parser.add_argument(
            "action",
            nargs="?",
            choices=("list", "show"),
            default="list",
            help="catalog action to run",
        )
        cmd_parser.add_argument(
            "profile_name",
            nargs="?",
            help="profile name for the show action",
        )
        return (
            cmd_parser,
            "bios-profile",
            "command read committed BIOS profile catalog entries",
        )

    @staticmethod
    def _profile_files(profile_dir):
        path = Path(profile_dir)
        if not path.exists() or not path.is_dir():
            return []
        return sorted(path.glob("*.json"))

    @staticmethod
    def _load_profile(path):
        return json.loads(path.read_text())

    @classmethod
    def _profiles(cls, profile_dir):
        return [cls._load_profile(path) for path in cls._profile_files(profile_dir)]

    @staticmethod
    def _summary(profile):
        return {key: profile.get(key) for key in SUMMARY_KEYS}

    @classmethod
    def _find_profile(cls, profiles, profile_name):
        for profile in profiles:
            if profile.get("name") == profile_name:
                return profile
        return None

    def execute(self,
                action: Optional[str] = "list",
                profile_name: Optional[str] = None,
                profile_dir: Optional[str] = PROFILE_DIR,
                filename: Optional[str] = None,
                data_type: Optional[str] = "json",
                verbose: Optional[bool] = False,
                do_async: Optional[bool] = False,
                do_expanded: Optional[bool] = False,
                **kwargs) -> CommandResult:
        """Return local BIOS profile summaries or one full profile."""
        profiles = self._profiles(profile_dir)
        error = None

        if action == "show":
            if not profile_name:
                data = {}
                error = "profile name is required for show"
            else:
                data = self._find_profile(profiles, profile_name) or {}
                if not data:
                    error = f"profile not found: {profile_name}"
        else:
            data = [self._summary(profile) for profile in profiles]

        save_if_needed(filename, data, data_format=data_type)
        return CommandResult(data, None, None, error)
