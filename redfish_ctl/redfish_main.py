"""Main entry for redfish_ctl

The main routine for redfish_ctl; the tool leverages the IDracManager class.
to interact with a Redfish BMC.

Each command registered dynamically and dispatch to respected execute method
by invoking requests through the manager.

Author Mus spyroot@gmail.com
"""

import argparse
import collections
import json
import logging
import os
import ssl
import sys
import warnings
from typing import Dict, Optional

import requests
import urllib3
from pygments import highlight

from .redfish_exceptions import RedfishException
from .version import __version__

try:
    from pygments.lexers.data import JsonLexer, YamlLexer
except ImportError:
    warnings.warn("Failed import json/yaml lexer from pygments.")

from pygments.formatters.terminal256 import Terminal256Formatter

from .cmd_exceptions import (
    AuthenticationFailed,
    FailedDiscoverAction,
    InvalidArgument,
    InvalidJsonSpec,
    JsonHttpError,
    MissingMandatoryArguments,
    MissingResource,
    ResourceNotFound,
    TaskIdUnavailable,
    UncommittedPendingChanges,
    UnsupportedAction,
)
from .cmd_utils import save_if_needed
from .config import ConfigurationConflict, endpoint_conflict_fields, endpoint_defaults
from .custom_argparser.customer_argdefault import CustomArgumentDefaultsHelpFormatter
from .idrac_manager import IDracManager
from .idrac_shared import RedfishAction, RedfishActionEncoder
from .redfish_query import RedfishQuery
from .telemetry import tracing
from .telemetry.exporter import apply_exporter_env_file, exporter_argv_uses_secret
from .vendors import VendorCapabilities, get_vendor

try:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    warnings.warn("Failed import urllib3")

logging.basicConfig(format='%(asctime)s,%(msecs)03d %(levelname)-8s '
                           '[%(filename)s:%(lineno)d] %(message)s',
                    datefmt='%Y-%m-%d:%H:%M:%S',
                    level=logging.ERROR)

logger = logging.getLogger(__name__)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.ERROR)
logger.addHandler(console_handler)

_ROOT_CONNECTION_ARGS = {
    "message_type",
    "redfish_host",
    "redfish_username",
    "redfish_password",
    "redfish_port",
    "idrac_ip",
    "idrac_username",
    "idrac_password",
    "idrac_port",
    "_endpoint_cli_overrides",
}

_LEGACY_ENDPOINT_ATTRS = {
    "idrac_ip": "redfish_host",
    "idrac_username": "redfish_username",
    "idrac_password": "redfish_password",
    "idrac_port": "redfish_port",
}

class _EndpointAliasAction(argparse.Action):
    """Argparse action that mirrors one option into canonical and legacy attrs."""

    def __init__(
            self, option_strings, dest, legacy_dest=None, endpoint_field=None,
            **kwargs):
        """Create a mirrored endpoint action.

        :param option_strings: option spellings handled by this action.
        :param dest: canonical argparse destination.
        :param legacy_dest: legacy argparse destination to mirror.
        :param endpoint_field: endpoint field this option explicitly overrides.
        :param kwargs: additional argparse action options.
        :return: None.
        """
        self.legacy_dest = legacy_dest
        self.endpoint_field = endpoint_field
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        """Store the parsed value under both endpoint destination names.

        :param parser: parser invoking the action.
        :param namespace: namespace being populated.
        :param values: parsed value for the option.
        :param option_string: option spelling used by the caller.
        :return: None. The namespace is updated in place.
        """
        setattr(namespace, self.dest, values)
        if self.legacy_dest is not None:
            setattr(namespace, self.legacy_dest, values)
        if self.endpoint_field is not None:
            overridden = set(
                getattr(namespace, "_endpoint_cli_overrides", set()) or set()
            )
            overridden.add(self.endpoint_field)
            setattr(namespace, "_endpoint_cli_overrides", overridden)


def _sync_legacy_endpoint_attrs(args: argparse.Namespace) -> None:
    """Mirror endpoint attrs between canonical and legacy Namespace names.

    :param args: parsed CLI namespace carrying root endpoint attrs.
    :return: None. The namespace is updated in place.
    """
    for legacy_attr, canonical_attr in _LEGACY_ENDPOINT_ATTRS.items():
        if hasattr(args, canonical_attr):
            setattr(args, legacy_attr, getattr(args, canonical_attr))
        elif hasattr(args, legacy_attr):
            setattr(args, canonical_attr, getattr(args, legacy_attr))


class TermColors:
    HEADER = '\033[95m'
    OK_BLUE = '\033[94m'
    OK_CYAN = '\033[96m'
    OK_GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


"note we do sub-string match"
TermList = ["xterm", "linux", "ansi", "xterm-256color"]
LOCAL_COMMANDS = {"bios-profile", "capability-report"}
_QUERY_CAPABILITY_FLAGS = (
    ("$select", "select", "query_select"),
    ("$filter", "filter", "query_filter"),
    ("$expand", "expand", "query_expand"),
    ("$top", "top", "query_top"),
    ("only", "only", "query_only"),
)
FLEET_COMMANDS = {"fleet"}


def color_printer(msg: str, tcolor: Optional[str] = TermColors.WARNING):
    """Printer error and if terminal support color print red or green etc.

    :param msg: the message to print.
    :param tcolor: terminal color escape applied when the terminal supports color.
    """
    current_term = os.getenv("TERM")
    if current_term is not None and current_term in TermList:
        err_msg = '{:<30}'.format(f"{tcolor}{msg}{TermColors.ENDC}")
        print(err_msg)
    else:
        print(msg)


def console_error_printer(msg):
    """Printer error and if terminal support color print red or green etc.

    :param msg: the error message to print.
    """
    color_printer(msg, tcolor=TermColors.WARNING)


def formatter(prog):
    """Construct an argparse help formatter for the given program name.

    :param prog: the program name passed to the help formatter.
    """
    argparse.HelpFormatter(prog, max_help_position=100, width=200)


class IdracDetails:
    """

    """
    version = __version__
    description = 'iDRAC command line tools.'
    author = 'Mus'
    author_email = 'spyroot@gmail.com'


__version__ = IdracDetails.version


def log_verbose(cmd_args, err):
    """Warn about a parse failure only when verbose output is enabled.

    :param cmd_args: parsed CLI arguments; ``verbose`` gates the warning.
    :param err: the exception whose message is included in the warning.
    """
    if cmd_args.verbose:
        err_msg = str(err)
        warnings.warn(f"Failed parse server respond. {err_msg}")
    return


def json_printer(json_data,
                 cmd_args: argparse.Namespace,
                 sort: Optional[bool] = True,
                 indents: Optional[int] = 4,
                 colorized: Optional[bool] = True,
                 header: Optional[str] = None,
                 footer: Optional[str] = None) -> None:
    """Json stdout printer.
    :param cmd_args:
    :param header:
    :param footer:
    :param colorized:
    :param json_data:
    :param indents:
    :param sort:
    :return:
    """
    if cmd_args.no_stdout:
        return

    json_raw = {}
    try:
        if isinstance(json_data, RedfishAction):
            json_raw = json_data.toJSON()
        elif isinstance(json_data, requests.models.Response):
            header = json_data.headers
            content_type = header.get('content-type')
            if content_type is not None:
                json_raw = json_data.json()
        elif isinstance(json_data, str):
            json_raw = json.dumps(
                json.loads(json_data), sort_keys=sort, indent=indents
            )
        else:
            json_raw = json.dumps(
                json_data, sort_keys=sort, indent=indents,
                cls=RedfishActionEncoder
            )

        # --yaml re-serializes the same payload as YAML instead of JSON.
        _lexer = JsonLexer()
        if getattr(cmd_args, "yaml", False):
            import yaml as _yaml
            _obj = json.loads(json_raw) if isinstance(json_raw, str) else json_raw
            json_raw = _yaml.safe_dump(
                _obj, sort_keys=bool(sort), default_flow_style=False)
            _lexer = YamlLexer()

        if len(json_raw) > 0:
            if header is not None and cmd_args.json_only is False:
                print(header)

            if colorized:
                colorful = highlight(
                    json_raw,
                    lexer=_lexer,
                    formatter=Terminal256Formatter())
                print(colorful)
            else:
                print(json_raw)

            if footer is not None:
                print(footer)
    except AttributeError as attr_err:
        log_verbose(cmd_args, attr_err)
        return
    except requests.exceptions.JSONDecodeError as rjde:
        log_verbose(cmd_args, rjde)
        return
    except json.decoder.JSONDecodeError as jde:
        log_verbose(cmd_args, jde)
        return


def process_respond(cmd_args, command_result):
    """Assemble the printable payload from a command result.

    :param cmd_args: parsed CLI arguments controlling which sections are emitted.
    :param command_result: the CommandResult returned by the invoked command.
    :return: ``command_result.data`` when ``--data_only`` is set, otherwise a dict
        with the data, extra, and discovered-action sections that pass the output filters.
    """
    query_request = {}
    if cmd_args.data_only:
        return command_result.data

    if command_result.data is not None:
        query_request["data"] = command_result.data
        query_request["idrac-data-description"] = "# idrac respond data for the command:"

    # extra data for deep walks
    if command_result.extra is not \
            None and cmd_args.no_extra is False:
        query_request["extra"] = command_result.extra
        query_request["idrac-extra-description"] = "# idrac respond extra data for the command:"
        # save extra as separate files.
        if hasattr(cmd_args, 'do_save') and cmd_args.do_save:
            for extra_k in command_result.extra.keys():
                if cmd_args.verbose:
                    logger.info(f"Saving {extra_k}.json")
                save_if_needed(f"{extra_k}.json", command_result.extra[extra_k])

    # discovered rest action.
    if command_result.discovered is not None and cmd_args.no_action is False:
        if cmd_args.json:
            if isinstance(command_result.discovered, dict):
                query_request["actions"] = {}
                for ak in command_result.discovered.keys():
                    query_request["actions-description"] = "# Redfish actions:"
                    if isinstance(command_result.discovered[ak], RedfishAction):
                        action = command_result.discovered[ak]
                        query_request["actions"][action.action_name] = action
                    else:
                        query_request["actions"][ak] = command_result.discovered[ak]
            else:
                query_request["actions"] = command_result.discovered

    return query_request


def is_network_scan(args) -> bool:
    """True when the invocation is a credential-less network-segment scan.

    ``bmc-scan`` always scans a segment; ``discovery`` scans only when
    ``--network`` is given. Both find BMCs on a segment with no target host and
    no credentials, so they must bypass the single-host REDFISH_IP credential gate
    and the startup ``check_api_version()`` / vendor probe (which would connect
    to a host that scan mode does not have).

    :param args: parsed CLI arguments.
    :return: True when the command is a credential-less network-segment scan.
    """
    sub = getattr(args, "subcommand", None)
    if sub == "bmc-scan":
        return True
    if sub == "discovery" and getattr(args, "scan_network", None):
        return True
    return False


def is_local_command(args) -> bool:
    """True when the command reads only local repository data.

    :param args: parsed CLI arguments.
    :return: True when the command reads only local data and needs no BMC connection.
    """
    subcommand = getattr(args, "subcommand", None)
    if subcommand == "bios-profile":
        return getattr(args, "action", "list") in {"list", "show"}
    return subcommand in LOCAL_COMMANDS


def _redfish_query_from_args(args: argparse.Namespace) -> RedfishQuery:
    """Build the global Redfish query object from top-level CLI flags.

    :param args: parsed CLI arguments carrying the top-level query_* flags.
    :return: a :class:`RedfishQuery` built from those flags.
    """
    return RedfishQuery(
        select=getattr(args, "query_select", None),
        filter=getattr(args, "query_filter", None),
        expand=getattr(args, "query_expand", None),
        expand_levels=getattr(args, "query_expand_levels", 1),
        top=getattr(args, "query_top", None),
        only=getattr(args, "query_only", False),
    )


def _query_flag_is_active(query: RedfishQuery, attr: str) -> bool:
    """Return whether a RedfishQuery field should be vendor-gated.

    :param query: the :class:`RedfishQuery` to inspect.
    :param attr: the query field name to test.
    :return: True when the field is set (``top`` counts as active when not None).
    """
    value = getattr(query, attr)
    if attr == "top":
        return value is not None
    return bool(value)


def _validate_redfish_query_for_vendor(
        query: RedfishQuery,
        caps: VendorCapabilities) -> None:
    """Raise when the target vendor profile does not support query flags.

    :param query: the :class:`RedfishQuery` to validate.
    :param caps: the target vendor's :class:`VendorCapabilities`.
    :raises InvalidArgument: when the vendor lacks support for a requested query flag.
    """
    unsupported = [
        name for name, attr, enabled_attr in _QUERY_CAPABILITY_FLAGS
        if _query_flag_is_active(query, attr) and not getattr(caps, enabled_attr)
    ]
    if unsupported:
        joined = ", ".join(unsupported)
        raise InvalidArgument(
            f"{caps.vendor} does not declare support for {joined}"
        )
    try:
        query.to_query_string(caps.one_query_param_per_uri)
    except ValueError as err:
        raise InvalidArgument(str(err)) from err


def is_fleet_command(args) -> bool:
    """True when the command manages its own per-node connections.

    :param args: parsed CLI arguments.
    :return: True when the command manages its own per-node connections.
    """
    return getattr(args, "subcommand", None) in FLEET_COMMANDS


def main(cmd_args: argparse.Namespace, command_name_to_cmd: Dict) -> None:
    """Main entry point.

    :param cmd_args: parsed CLI arguments for the selected command.
    :param command_name_to_cmd: mapping of command name to its (type, name) tuple.
    """
    # BMCs ship self-signed certs, so verification is opt-in via --verify-ssl.
    # We skip verification by default; --insecure stays as an explicit "skip".
    verify_ssl = getattr(cmd_args, "verify_ssl", False)
    insecure = not verify_ssl

    if insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Optional OTLP span pipeline for this run; no-op unless --otlp-traces is set.
    # Endpoint/headers resolve from OTEL_EXPORTER_OTLP_* (point them at the Splunk O11y
    # OTLP ingest with an X-SF-Token header); deployment.environment and other resource
    # keys come from OTEL_RESOURCE_ATTRIBUTES. service.name defaults to the shared
    # redfish_ctl identity so spans and hw.* metrics land on one APM service node.
    if getattr(cmd_args, "otlp_traces", False):
        tracing.setup_otlp()
        tracing.install_termination_flush()

    # the manager is the main interface main uses to interact with the BMC.
    redfish_api = IDracManager(host=cmd_args.redfish_host,
                                     username=cmd_args.redfish_username,
                                     password=cmd_args.redfish_password,
                                     port=cmd_args.redfish_port,
                                     insecure=insecure,
                                     is_http=cmd_args.use_http,
                                     is_debug=cmd_args.debug)

    connectionless_mode = (
        is_network_scan(cmd_args)
        or is_local_command(cmd_args)
        or is_fleet_command(cmd_args)
    )

    if cmd_args.subcommand not in command_name_to_cmd:
        console_error_printer("Error: Unknown command.")
        return
    cmd = command_name_to_cmd[cmd_args.subcommand]

    # One operation ROOT span named by the command (static, resolved via the
    # register chain); preflight, dispatch, and post-processing all nest under it,
    # so there are no orphan roots. The finally flushes finished spans as the
    # stack unwinds on every exit path -- normal return, exception, SIGINT, and
    # SIGTERM (see install_termination_flush). Everything is a no-op when tracing
    # is off.
    try:
        with tracing.operation_span(cmd.name) as root_span:
            _run(cmd, cmd_args, redfish_api, insecure, connectionless_mode, root_span)
    finally:
        tracing.shutdown()


def _run(cmd, cmd_args, redfish_api, insecure, connectionless_mode, root_span):
    """Execute one resolved command inside the operation root span opened by main.

    Split out of ``main`` so the version handshake, dispatch, and Dell
    post-processing all run inside the single ``operation_span`` (no orphan
    roots). Exceptions are recorded on ``root_span`` here because they are handled
    (printed) locally and would otherwise never reach the span.

    :param cmd: the resolved ``(type, name)`` command from the register chain.
    :param cmd_args: parsed CLI arguments.
    :param redfish_api: the manager used to talk to the BMC.
    :param insecure: True when TLS certificate verification is skipped.
    :param connectionless_mode: True for scan/local/fleet commands (no BMC host).
    :param root_span: the operation root span, or None when tracing is off.
    :return: None.
    """
    # Version handshake / vendor probe -- the root's first CLIENT child. A network
    # scan has no single target host, so it is skipped.
    if not connectionless_mode:
        _ = redfish_api.check_api_version()

    if cmd_args.verbose:
        logger.info("verbose is set on")

    try:
        arg_dict = dict(
            (k, v) for k, v in vars(cmd_args).items()
            if k not in _ROOT_CONNECTION_ARGS
        )

        redfish_query = _redfish_query_from_args(cmd_args)
        if not connectionless_mode and not redfish_query.is_empty():
            query_caps = get_vendor(redfish_api.redfish_vendor)
            _validate_redfish_query_for_vendor(redfish_query, query_caps)
            arg_dict["redfish_query"] = redfish_query
            arg_dict["redfish_query_one_param_per_uri"] = (
                query_caps.one_query_param_per_uri
            )

        if cmd_args.verbose:
            json_printer(arg_dict, cmd_args, colorized=cmd_args.nocolor)

        # invoke cmd
        if connectionless_mode:
            # Connectionless dispatch: inject empty connection fields (invoke
            # pops them) without the non-empty credential check that sync_invoke
            # enforces. Segment scans and local catalog reads need no host.
            invoke_kwargs = dict(arg_dict)
            invoke_kwargs.update({
                "_redfish_host": cmd_args.redfish_host or "",
                "_redfish_username": cmd_args.redfish_username or "",
                "_redfish_password": cmd_args.redfish_password or "",
                "_redfish_port": cmd_args.redfish_port,
                "insecure": insecure,
                "is_http": cmd_args.use_http,
            })
            command_result = redfish_api.invoke(cmd.type, cmd.name, **invoke_kwargs)
        else:
            command_result = redfish_api.sync_invoke(
                cmd.type, cmd.name, **arg_dict
            )

        if not connectionless_mode and redfish_api.redfish_vendor == "Dell":
            if isinstance(command_result.data, dict):
                command_result.data["idrac_version"] = redfish_api.idrac_manager_version
                command_result.data["redfish_version"] = redfish_api.redfish_version
            # if isinstance(command_result.data, list) and len(command_result.data) > 0:
            #     command_result.data[0]["idrac_version"] = redfish_api.idrac_manager_version
            #     command_result.data[0]["redfish_version"] = redfish_api.redfish_version

        tracing.record_result(root_span, command_result)

        if command_result.error is not None:
            json_printer(command_result.data, cmd_args, colorized=cmd_args.nocolor)
            if isinstance(command_result.error, JsonHttpError):
                console_error_printer(command_result.error.json_error)
            return

        processed_data = process_respond(cmd_args, command_result)
        if json_printer:
            json_printer(processed_data, cmd_args, colorized=cmd_args.nocolor)

    except RedfishException as redfish_err:
        tracing.record_exception(root_span, redfish_err)
        console_error_printer(f"Error: {redfish_err}")
    except TaskIdUnavailable as tid:
        tracing.record_exception(root_span, tid)
        console_error_printer(f"Error: {tid}")
    except MissingResource as mr:
        tracing.record_exception(root_span, mr)
        console_error_printer(f"Error: {mr}")
    except InvalidJsonSpec as ijs:
        tracing.record_exception(root_span, ijs)
        console_error_printer(f"Error: {ijs}")
    except ResourceNotFound as rnf:
        tracing.record_exception(root_span, rnf)
        console_error_printer(f"Error: {rnf}")
    except InvalidArgument as ia:
        tracing.record_exception(root_span, ia)
        console_error_printer(f"Error: {ia}")
    except FailedDiscoverAction as fda:
        tracing.record_exception(root_span, fda)
        console_error_printer(f"Error: {fda}")
    except UnsupportedAction as ua:
        tracing.record_exception(root_span, ua)
        console_error_printer(f"Error:{ua}")
    except MissingMandatoryArguments as mmr:
        tracing.record_exception(root_span, mmr)
        console_error_printer(f"Error:{mmr}")
    except FileNotFoundError as fne:
        tracing.record_exception(root_span, fne)
        console_error_printer(f"Error:{fne}")
    except UncommittedPendingChanges as upc:
        tracing.record_exception(root_span, upc)
        console_error_printer(f"Error:{upc}")


def create_cmd_tree(arg_parser, debug=False) -> Dict:
    """Create command tree structure.

    :param arg_parser: the root argument parser to attach subcommand parsers to.
    :param debug: when True, log each registered command.
    :return: a dict that store mapping for each command.
    """
    redfish_api = IDracManager()
    command_name_to_cmd = {}
    commands_registry = redfish_api.get_registry()
    command_name = collections.namedtuple("Command", "type name")

    subparsers = arg_parser.add_subparsers(
        title='main command',
        metavar="main command",
        help='list of redfish_ctl commands',
        dest="subcommand",
        description='''Each action requires choosing
        a main command bios, boot, etc|n
        Example: redfish_ctl bios\n''',
        required=True
    )

    for k in commands_registry:
        for sub_key in commands_registry[k]:
            cls = commands_registry[k][sub_key]
            if debug:
                logger.debug(f"Registering command {k} {sub_key}")
            if hasattr(cls, "register_subcommand"):
                # register each command
                cli_arg_parser, cmd_name, cmd_help = cls.register_subcommand(cls)
                if debug:
                    logger.debug(f"Registering command name {cmd_name} {cmd_help}")

                subparsers.add_parser(
                    cmd_name,
                    parents=[cli_arg_parser],
                    help=f"{str(cmd_help)}",
                    formatter_class=CustomArgumentDefaultsHelpFormatter,
                )
                command_name_to_cmd[cmd_name] = command_name(k, sub_key)

    return command_name_to_cmd


def redfish_main_ctl():
    """
    """
    logger.setLevel(logging.ERROR)
    endpoint_conflicts = set()
    try:
        endpoint = endpoint_defaults()
    except ConfigurationConflict:
        endpoint = endpoint_defaults(strict=False)
        endpoint_conflicts = endpoint_conflict_fields()
    except ValueError as err:
        console_error_printer(f"Error: {err}")
        sys.exit(1)

    parser = argparse.ArgumentParser(
        prog="redfish_ctl", add_help=True,
        description='''redfish_ctl - a vendor-neutral command-line tool to drive server BMCs |n
                                     over the Redfish REST API: Dell iDRAC, Supermicro, HPE iLO, and |n
                                     generic DMTF Redfish. Supports both synchronous and asynchronous |n
                                     calls. (idrac_ctl remains a backward-compatible alias.)''',
        epilog='''Docs and examples: https://github.com/spyroot/redfish_ctl |n
                                             The examples/ folder contains many ready-to-run scripts. |n
                                             Author: Mustafa Bayramov <spyroot@gmail.com>
                                             ''',
        formatter_class=CustomArgumentDefaultsHelpFormatter)

    credentials = parser.add_argument_group('credentials', '# Redfish BMC credentials.')

    # Root endpoint/credential flags. The deprecated --idrac_* spellings remain
    # accepted as aliases, but the neutral spellings are shown first and stored
    # separately from subcommand-local --host/--port options.
    credentials.add_argument(
        '--host', '--idrac_ip', required=False, type=str,
        action=_EndpointAliasAction, legacy_dest="idrac_ip",
        endpoint_field="host",
        dest="redfish_host", default=endpoint.host,
        help="BMC host or IP address, by default "
             "read from environment REDFISH_IP (or legacy IDRAC_IP).")
    credentials.add_argument(
        '--username', '--idrac_username', required=False, type=str,
        action=_EndpointAliasAction, legacy_dest="idrac_username",
        endpoint_field="username",
        dest="redfish_username", default=endpoint.username,
        help="BMC username, by default "
             "read from environment REDFISH_USERNAME (or legacy IDRAC_USERNAME).")
    credentials.add_argument(
        '--password', '--idrac_password', required=False, type=str,
        action=_EndpointAliasAction, legacy_dest="idrac_password",
        endpoint_field="password",
        dest="redfish_password", default=endpoint.password,
        help="BMC password, by default "
             "read from environment REDFISH_PASSWORD (or legacy IDRAC_PASSWORD).")
    credentials.add_argument(
        '--port', '--idrac_port', required=False, type=int,
        action=_EndpointAliasAction, legacy_dest="idrac_port",
        endpoint_field="port",
        dest="redfish_port", default=endpoint.port,
        help="BMC port, by default "
             "read from environment REDFISH_PORT (or legacy IDRAC_PORT).")
    parser.set_defaults(
        idrac_ip=endpoint.host,
        idrac_username=endpoint.username,
        idrac_password=endpoint.password,
        idrac_port=endpoint.port,
        _endpoint_cli_overrides=set(),
    )
    credentials.add_argument(
        '--insecure', action='store_true', required=False, default=False,
        help="skip TLS certificate verification (explicit form of the "
             "default; BMCs ship self-signed certs).")
    credentials.add_argument(
        '--verify-ssl', dest='verify_ssl',
        action='store_true', required=False, default=False,
        help="verify the BMC TLS certificate. Off by default because "
             "BMCs ship self-signed certs; opt in only with a trusted cert.")
    credentials.add_argument(
        '--use_http', action='store_true', required=False, default=False,
        help="use http instead https as transport.")

    redfish_query = parser.add_argument_group(
        'redfish query', '# server-side Redfish GET query options')
    redfish_query.add_argument(
        '--select', dest='query_select', required=False, default=None,
        help="apply Redfish $select to GET commands, for example Id,Name.")
    redfish_query.add_argument(
        '--filter', dest='query_filter', required=False, default=None,
        help="apply Redfish $filter to GET commands.")
    redfish_query.add_argument(
        '--expand', dest='query_expand', nargs='?', const='*',
        choices=['*', '.', '~'], required=False, default=None,
        help="apply Redfish $expand to GET commands; mode defaults to '*'.")
    redfish_query.add_argument(
        '--expand-levels', dest='query_expand_levels', required=False,
        type=int, default=1,
        help="Redfish $expand levels when --expand is used.")
    redfish_query.add_argument(
        '--top', dest='query_top', required=False, type=int, default=None,
        help="apply Redfish $top to GET commands.")
    redfish_query.add_argument(
        '--only', dest='query_only', action='store_true',
        required=False, default=False,
        help="apply the Redfish only query parameter to GET commands.")

    verbose_group = parser.add_argument_group('verbose', '# verbose and debug options')
    verbose_group.add_argument(
        '--debug', action='store_true', required=False,
        help="enables debug.")
    verbose_group.add_argument(
        '--verbose', action='store_true', required=False, default=False,
        help="enables verbose output.")
    verbose_group.add_argument(
        '--log', required=False, default=logging.NOTSET,
        help="log level.")
    verbose_group.add_argument(
        '--otlp-traces', action='store_true', required=False, default=False,
        dest="otlp_traces",
        help="emit OpenTelemetry spans for this run over OTLP "
             "(honours OTEL_EXPORTER_OTLP_*; needs the [otlp] extra).")

    # controls for output
    output_controllers = parser.add_argument_group('output', '# output controller options')
    output_controllers.add_argument(
        '--no_extra', action='store_true', required=False, default=False,
        help="disables extra data stdout output.")
    output_controllers.add_argument(
        '--no_action', action='store_true', required=False, default=False,
        help="disables rest action data stdout output.")
    output_controllers.add_argument(
        '--json', action='store_true', required=False, default=True,
        help="by default we use json to output to console.")
    output_controllers.add_argument(
        '--yaml', action='store_true', required=False, default=False,
        dest='yaml',
        help="render the output as YAML instead of JSON.")
    output_controllers.add_argument(
        '--json_only', action='store_true', required=False, default=False,
        help="by default output has different section. "
             "--json_only will merge all in one single output.")
    output_controllers.add_argument(
        '-d', '--data_only', action='store_true', required=False, default=False,
        help="for commands where we only need single value from json.")
    output_controllers.add_argument(
        '--no-stdout', '--no_stdout', action='store_true', required=False, default=False,
        help="by default we use stdout output.")
    output_controllers.add_argument(
        '--nocolor', action='store_false', required=False, default=True,
        help="by default output to terminal is colorful.")

    output_controllers.add_argument(
        '-f', '--filename', required=False, type=str,
        default="", help="Filename if we need save to a file.")
    parser.add_argument('-v', '--version', action='version',
                        version="%(prog)s " + __version__)

    cmd_dict = create_cmd_tree(parser)
    args = parser.parse_args()
    _sync_legacy_endpoint_attrs(args)
    unresolved_endpoint_conflicts = (
        endpoint_conflicts - getattr(args, "_endpoint_cli_overrides", set())
    )
    if unresolved_endpoint_conflicts:
        console_error_printer(
            "Error: conflicting REDFISH_* and IDRAC_* endpoint environment "
            "values; supply explicit CLI flags for "
            f"{', '.join(sorted(unresolved_endpoint_conflicts))} or unset the "
            "legacy aliases."
        )
        sys.exit(1)
    if args.debug:
        logger.setLevel(args.log)

    if getattr(args, "subcommand", None) == "exporter":
        if exporter_argv_uses_secret(sys.argv):
            print(
                "Please provide exporter credentials through environment "
                "variables or --credential-file, not --password/--idrac_password."
            )
            sys.exit(2)
        try:
            apply_exporter_env_file(args)
            _sync_legacy_endpoint_attrs(args)
        except ConfigurationConflict as err:
            console_error_printer(f"Error: {err}")
            sys.exit(1)
        except FileNotFoundError as fne:
            console_error_printer(f"Error:{fne}")
            sys.exit(1)

    # A network-segment scan (bmc-scan, or discovery --network) has no single
    # target host and uses no credentials, so it skips the REDFISH_IP credential
    # gate that every host-targeted command requires.
    if (
        not is_network_scan(args)
        and not is_local_command(args)
        and not is_fleet_command(args)
    ):
        if args.redfish_host is None or len(args.redfish_host) == 0:
            print(
                "Please indicate the Redfish host. "
                "--host or set REDFISH_IP environment variable. "
                "(export REDFISH_IP=ip_address)"
            )
            sys.exit(1)
        if args.redfish_username is None or len(args.redfish_username) == 0:
            print(
                "Please indicate the Redfish username."
                "--username or set REDFISH_USERNAME environment variable. "
                "(export REDFISH_USERNAME=root)"
            )
            sys.exit(1)
        if args.redfish_password is None or len(args.redfish_password) == 0:
            print(
                "Please indicate the Redfish password. "
                "--password or set REDFISH_PASSWORD environment."
                "(export REDFISH_PASSWORD=<password>)"
            )
            sys.exit(1)
    try:
        main(args, cmd_dict)
    except AuthenticationFailed as af:
        console_error_printer(f"Error: {af}")
    except requests.exceptions.ConnectionError as http_error:
        console_error_printer(f"Error: {http_error}")
    except ssl.SSLCertVerificationError as ssl_err:
        console_error_printer(f"Error: {ssl_err}")


if __name__ == "__main__":
    # Allow running the CLI without the installed console script, e.g.
    #   python -m redfish_ctl.redfish_main discovery --network 192.168.254.0/24
    redfish_main_ctl()
