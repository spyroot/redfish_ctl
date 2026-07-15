"""Read the committed BIOS tuning profile catalog.

    redfish_ctl bios-profile list
    redfish_ctl bios-profile show <name>
    redfish_ctl bios-profile diff <name>
"""

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
        """Construct the bios-profile command, forwarding credentials to the base manager."""
        super(BiosProfile, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the ``bios-profile`` catalog subcommand.

        :return: tuple of (ArgumentParser, command name, command help).
        """
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
        """Return the sorted ``*.json`` profile files under ``profile_dir``.

        :param profile_dir: directory to scan for profile JSON files.
        :return: sorted list of profile file paths, or empty when the directory is absent.
        """
        path = Path(profile_dir)
        if not path.exists() or not path.is_dir():
            return []
        return sorted(path.glob("*.json"))

    @staticmethod
    def _load_profile(path):
        """Parse a single profile JSON file.

        :param path: path to the profile JSON file.
        :return: the profile decoded as a dict.
        """
        return json.loads(path.read_text())

    @classmethod
    def _profiles(cls, profile_dir):
        """Load every profile found under ``profile_dir``.

        :param profile_dir: directory to scan for profile JSON files.
        :return: list of decoded profile dicts.
        """
        return [cls._load_profile(path) for path in cls._profile_files(profile_dir)]

    @staticmethod
    def _summary(profile):
        """Reduce a profile to its catalog summary fields.

        :param profile: a decoded profile dict.
        :return: dict of the summary keys (name, vendor, model, description, risk).
        """
        return {key: profile.get(key) for key in SUMMARY_KEYS}

    @staticmethod
    def _profile_identity(profile):
        """Reduce a profile to its identity fields.

        :param profile: a decoded profile dict.
        :return: dict of the identity keys (name, vendor, model, risk).
        """
        return {key: profile.get(key) for key in PROFILE_ID_KEYS}

    @classmethod
    def _find_profile(cls, profiles, profile_name):
        """Find a profile by its ``name`` field.

        :param profiles: list of decoded profile dicts to search.
        :param profile_name: profile name to match.
        :return: the matching profile dict, or None when none matches.
        """
        for profile in profiles:
            if profile.get("name") == profile_name:
                return profile
        return None

    @staticmethod
    def _attributes_from_bios_result(result):
        """Extract the attribute mapping from a bios query result.

        :param result: the CommandResult returned by the bios inventory query.
        :return: the ``Attributes`` dict when present, otherwise the result data dict.
        """
        data = result.data if isinstance(result.data, dict) else {}
        attributes = data.get("Attributes")
        if isinstance(attributes, dict):
            return attributes
        return data

    @classmethod
    def _diff_profile(cls, profile, current_attributes):
        """Compare a profile's desired attributes against the current values.

        :param profile: a decoded profile dict holding the desired attributes.
        :param current_attributes: the host's current BIOS attribute values.
        :return: a diff dict with the profile identity, an overall ``matches`` flag,
            per-status counts, and a per-attribute status row list.
        """
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
        """Read the host's current values for the profile's attributes.

        :param profile: a decoded profile dict whose attribute names are queried.
        :param verbose: enables verbose output.
        :param do_async: note async will subscribe to an event loop.
        :return: the current BIOS attribute values as a dict.
        """
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
        """Wrap a profile's attributes as a bios-change payload.

        :param profile: a decoded profile dict.
        :return: a ``{"Attributes": {...}}`` change payload, or empty when the
            profile has no attributes mapping.
        """
        attributes = profile.get("attributes")
        return {"Attributes": attributes} if isinstance(attributes, dict) else {}

    def _apply_profile(self, profile, confirm, dry_run, do_async):
        """Stage a profile through bios-change, capturing a rollback snapshot first.

        Builds the change payload from the profile; when the profile has no
        attributes it returns early as a dry run. Otherwise it snapshots the
        current values as a rollback spec and invokes bios-change, which only
        applies (rather than previews) when ``confirm`` is set and ``dry_run``
        is not.

        :param profile: a decoded profile dict holding the attributes to stage.
        :param confirm: when True (and not a dry run) apply the change instead of previewing.
        :param dry_run: preview the staged payload even when confirm is set.
        :param do_async: note async will subscribe to an event loop.
        :return: CommandResult with the profile name, dry-run flag, change,
            rollback, and staged payloads, plus any rollback or staging error.
        """
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
        """Return local BIOS profile summaries or one full profile.

        :param action: catalog action to run: ``list``, ``show``, ``diff``, or ``apply``.
        :param profile_name: profile name required by the show, diff, and apply actions.
        :param profile_dir: directory the profile catalog is read from.
        :param confirm: for ``apply``, stage the profile through bios-change instead of previewing.
        :param dry_run: for ``apply``, preview the staged payload even when confirm is set.
        :param filename: if set, save the response to this file.
        :param data_type: json or xml.
        :param verbose: enables verbose output.
        :param do_async: note async will subscribe to an event loop.
        :param do_expanded: accepted for CLI compatibility; not used by this command.
        :return: CommandResult holding the summaries, profile, diff, or apply result.
        """
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
