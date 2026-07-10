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
PROFILE_ID_KEYS = ("name", "vendor", "model", "risk")


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
            choices=("list", "show", "diff"),
            default="list",
            help="catalog action to run",
        )
        cmd_parser.add_argument(
            "profile_name",
            nargs="?",
            help="profile name for the show or diff action",
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

    @staticmethod
    def _profile_identity(profile):
        return {key: profile.get(key) for key in PROFILE_ID_KEYS}

    @classmethod
    def _find_profile(cls, profiles, profile_name):
        for profile in profiles:
            if profile.get("name") == profile_name:
                return profile
        return None

    @staticmethod
    def _attributes_from_bios_result(result):
        data = result.data if isinstance(result.data, dict) else {}
        attributes = data.get("Attributes")
        if isinstance(attributes, dict):
            return attributes
        return data

    @classmethod
    def _diff_profile(cls, profile, current_attributes):
        rows = []
        summary = {
            "total": 0,
            "matching": 0,
            "different": 0,
            "missing": 0,
        }
        for attribute, desired in profile.get("attributes", {}).items():
            missing = attribute not in current_attributes
            current = current_attributes.get(attribute)
            if missing:
                status = "missing"
            elif current == desired:
                status = "matching"
            else:
                status = "different"
            summary["total"] += 1
            summary[status] += 1
            rows.append({
                "attribute": attribute,
                "current": current,
                "desired": desired,
                "status": status,
            })

        return {
            "profile": cls._profile_identity(profile),
            "matches": (
                summary["different"] == 0
                and summary["missing"] == 0
            ),
            "summary": summary,
            "attributes": rows,
        }

    def _read_current_attributes(self, profile, verbose, do_async):
        attribute_names = ",".join(profile.get("attributes", {}).keys())
        result = self.sync_invoke(
            ApiRequestType.BiosQuery,
            "bios_inventory",
            attr_only=True,
            attr_filter=attribute_names,
            verbose=verbose,
            do_async=do_async,
        )
        return self._attributes_from_bios_result(result)

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

        if action in {"show", "diff"}:
            if not profile_name:
                data = {}
                error = f"profile name is required for {action}"
            else:
                profile = self._find_profile(profiles, profile_name)
                if not profile:
                    data = {}
                    error = f"profile not found: {profile_name}"
                elif action == "show":
                    data = profile
                else:
                    current_attributes = self._read_current_attributes(
                        profile, verbose, do_async
                    )
                    data = self._diff_profile(profile, current_attributes)
        else:
            data = [self._summary(profile) for profile in profiles]

        save_if_needed(filename, data, data_format=data_type)
        return CommandResult(data, None, None, error)
