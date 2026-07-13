"""Local corpus library command."""
from __future__ import annotations

import argparse
from abc import abstractmethod

from . import corpora
from .base_manager import CommandBase
from .command_shared import ApiRequestType, Singleton
from .redfish_manager import CommandResult


class Corpus(CommandBase,
             scm_type=ApiRequestType.Corpus,
             name="corpus",
             metaclass=Singleton):
    """Inspect and materialize the committed Redfish corpus library."""

    def __init__(self, *args, **kwargs):
        super(Corpus, self).__init__(*args, **kwargs)

    @staticmethod
    @abstractmethod
    def register_subcommand(cls):
        """Register the local corpus command."""
        cmd_parser = argparse.ArgumentParser(add_help=False)
        sub = cmd_parser.add_subparsers(dest="action", required=True)

        def add_filters(parser):
            corpora.add_filters(parser)

        list_cmd = sub.add_parser("list", help="list corpus manifest rows")
        add_filters(list_cmd)
        list_cmd.add_argument("--format", choices=("table", "json"), default="table")

        pull_cmd = sub.add_parser("pull", help="pull selected Git-LFS archives")
        add_filters(pull_cmd)

        verify_cmd = sub.add_parser("verify", help="verify selected archives")
        add_filters(verify_cmd)
        verify_cmd.add_argument("--format", choices=("table", "json"), default="table")
        verify_cmd.add_argument(
            "--require-materialized",
            action="store_true",
            help="fail on missing Git-LFS objects or bare pointers")

        materialize_cmd = sub.add_parser("materialize", help="extract archives by stable slug")
        add_filters(materialize_cmd)
        materialize_cmd.add_argument(
            "--dest", "--output", dest="dest", required=True,
            help="destination directory")

        extract_cmd = sub.add_parser("extract-all", help="extract archives by stable slug")
        add_filters(extract_cmd)
        extract_cmd.add_argument("--dest", required=True, help="destination directory")

        files_cmd = sub.add_parser("files", help="list materialized file names")
        add_filters(files_cmd)
        files_cmd.add_argument("--limit", type=int, default=0, help="maximum files to list")
        files_cmd.add_argument("--format", choices=("table", "json"), default="table")

        return (
            cmd_parser,
            "corpus",
            "command inspect and materialize Redfish corpus archives",
        )

    def execute(self, **kwargs) -> CommandResult:
        """Run a corpus action without BMC access."""
        action = kwargs.pop("action")
        args = argparse.Namespace(action=action, **kwargs)
        return CommandResult(corpora.dispatch(args), None, None, None)
