"""Read the committed BIOS tuning profile catalog."""

import json
from abc import abstractmethod
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

from ..cmd_utils import save_if_needed
from ..redfish_manager_base import RedfishManagerBase
from ..redfish_manager_shared import ApiRequestType, Singleton
from ..redfish_manager import CommandResult

PROFILE_DIR = Path(__file__).resolve().parents[2] / "specs" / "profiles"
SUMMARY_KEYS = ("name", "vendor", "model", "description", "risk")
PROFILE_ID_KEYS = ("name", "vendor", "model", "risk")


class BiosProfile(RedfishManagerBase,
                  scm_type=ApiRequestType.BiosProfile,
                  name="bios-profile",
                  metaclass=Singleton):
    """Read or stage local BIOS profile specifications."""

    def __init__(self, *args, **kwargs):
        super(BiosProfile, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``bios-profile`` catalog subcommand."""
        cmd_parser = cls.base_parser(is_async=False, is_expanded=False)
        cmd_parser.add_argument(
            "action",
            nargs="?",
            choices=("list", "show", "diff", "apply"),
            default="list",
            help="catalog action to run",
        )
        cmd_parser.add_argument(
            "profile_name",
            nargs="?",
            help="profile name for the show, diff, or apply action",
        )
        cmd_parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="stage the selected profile through bios-change",
        )
        cmd_parser.add_argument(
            "--dry_run",
            action="store_true",
            default=False,
            help="preview the staged payload even when --confirm is present",
        )
        return (
            cmd_parser,
            "bios-profile",
            "command read or stage committed BIOS profile catalog entries",
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

    @staticmethod
    def _profile_change(profile):
        attributes = profile.get("attributes")
        return {"Attributes": attributes} if isinstance(attributes, dict) else {}

    def _apply_profile(self, profile, confirm, dry_run, do_async):
        change = self._profile_change(profile)
        if not change.get("Attributes"):
            return CommandResult(
                {
                    "profile": profile.get("name"),
                    "dry_run": True,
                    "change": change,
                    "rollback": {"Attributes": {}},
                    "staged": {},
                },
                None,
                None,
                "profile has no attributes",
            )

        with TemporaryDirectory() as tmp_dir:
            spec_path = Path(tmp_dir) / f"{profile.get('name', 'profile')}.json"
            spec_path.write_text(json.dumps(change))

            rollback = self.sync_invoke(
                ApiRequestType.BiosSnapshot,
                "bios_snapshot",
                from_spec=str(spec_path),
                do_async=do_async,
            )
            preview = bool(dry_run or not confirm)
            staged = self.sync_invoke(
                ApiRequestType.BiosChangeSettings,
                "bios_change_settings",
                from_spec=str(spec_path),
                apply="on-reset",
                do_show=preview,
                do_async=do_async,
            )

        data = {
            "profile": profile.get("name"),
            "dry_run": preview,
            "change": change,
            "rollback": rollback.data if rollback else {"Attributes": {}},
            "staged": staged.data if staged else {},
        }
        error = rollback.error if rollback and rollback.error is not None else None
        if error is None and staged and staged.error is not None:
            error = staged.error
        return CommandResult(data, None, None, error)

    def execute(self,
                action: Optional[str] = "list",
                profile_name: Optional[str] = None,
                profile_dir: Optional[str] = PROFILE_DIR,
                confirm: Optional[bool] = False,
                dry_run: Optional[bool] = False,
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
        elif action == "apply":
            if not profile_name:
                data = {}
                error = "profile name is required for apply"
            else:
                profile = self._find_profile(profiles, profile_name)
                if not profile:
                    data = {}
                    error = f"profile not found: {profile_name}"
                else:
                    result = self._apply_profile(
                        profile, bool(confirm), bool(dry_run), do_async
                    )
                    save_if_needed(filename, result.data, data_format=data_type)
                    return result
        else:
            data = [self._summary(profile) for profile in profiles]

        save_if_needed(filename, data, data_format=data_type)
        return CommandResult(data, None, None, error)
