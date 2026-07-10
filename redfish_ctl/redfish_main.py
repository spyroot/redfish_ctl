"""Main entry for redfish_ctl

The main routine for redfish_ctl , ctl tool leverages iDRAC Manager class.
to interact with Dell IDRAC.

Each command registered dynamically and dispatch to respected execute method
by invoking request from IDRAC Manager.

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
from .custom_argparser.customer_argdefault import CustomArgumentDefaultsHelpFormatter
from .idrac_manager import IDracManager
from .idrac_shared import RedfishAction, RedfishActionEncoder
from .redfish_query import RedfishQuery
from .telemetry.exporter import apply_exporter_env_file, exporter_argv_uses_secret
from .vendors import VendorCapabilities, get_vendor


def _env(*names, default=""):
    """Return the first non-empty environment variable among ``names``.

    Used to read the going-forward ``REDFISH_*`` endpoint/credential variables while
    still honoring the legacy ``IDRAC_*`` names during the rename: pass the REDFISH_
    name first, the IDRAC_ name second, so REDFISH_ wins when both are set.
    """
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


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
LOCAL_COMMANDS = {"bios-profile"}
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
    """
    current_term = os.getenv("TERM")
    if current_term is not None and current_term in TermList:
        err_msg = '{:<30}'.format(f"{tcolor}{msg}{TermColors.ENDC}")
        print(err_msg)
    else:
        print(msg)


def console_error_printer(msg):
    """Printer error and if terminal support color print red or green etc.
    """
    color_printer(msg, tcolor=TermColors.WARNING)


def formatter(prog):
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
    """
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
    no credentials, so they must bypass the single-host IDRAC_IP/credential gate
    and the startup ``check_api_version()`` / vendor probe (which would connect
    to a host that scan mode does not have).
    """
    sub = getattr(args, "subcommand", None)
    if sub == "bmc-scan":
        return True
    if sub == "discovery" and getattr(args, "scan_network", None):
        return True
    return False


def is_local_command(args) -> bool:
    """True when the command reads only local repository data."""
    subcommand = getattr(args, "subcommand", None)
    if subcommand == "bios-profile":
        return getattr(args, "action", "list") in {"list", "show"}
    return subcommand in LOCAL_COMMANDS


def _redfish_query_from_args(args: argparse.Namespace) -> RedfishQuery:
    """Build the global Redfish query object from top-level CLI flags."""
    return RedfishQuery(
        select=getattr(args, "query_select", None),
        filter=getattr(args, "query_filter", None),
        expand=getattr(args, "query_expand", None),
        expand_levels=getattr(args, "query_expand_levels", 1),
        top=getattr(args, "query_top", None),
        only=getattr(args, "query_only", False),
    )


def _query_flag_is_active(query: RedfishQuery, attr: str) -> bool:
    """Return whether a RedfishQuery field should be vendor-gated."""
    value = getattr(query, attr)
    if attr == "top":
        return value is not None
    return bool(value)


def _validate_redfish_query_for_vendor(
        query: RedfishQuery,
        caps: VendorCapabilities) -> None:
    """Raise when the target vendor profile does not support query flags."""
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
    """True when the command manages its own per-node connections."""
    return getattr(args, "subcommand", None) in FLEET_COMMANDS


def main(cmd_args: argparse.Namespace, command_name_to_cmd: Dict) -> None:
    """Main entry point
    """
    # BMCs ship self-signed certs, so verification is opt-in via --verify-ssl.
    # We skip verification by default; --insecure stays as an explicit "skip".
    verify_ssl = getattr(cmd_args, "verify_ssl", False)
    insecure = not verify_ssl

    if insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # idrac manager main interface main uses to interact with IDRAC.
    redfish_api = IDracManager(idrac_ip=cmd_args.idrac_ip,
                               idrac_username=cmd_args.idrac_username,
                               idrac_password=cmd_args.idrac_password,
                               idrac_port=cmd_args.idrac_port,
                               insecure=insecure,
                               is_http=cmd_args.use_http,
                               is_debug=cmd_args.debug)

    # A network scan has no single target host, so skip the version handshake
    # (it would try to connect to a host we do not have).
    scan_mode = is_network_scan(cmd_args)
    local_mode = is_local_command(cmd_args)
    fleet_mode = is_fleet_command(cmd_args)
    connectionless_mode = scan_mode or local_mode or fleet_mode
    if not connectionless_mode:
        _ = redfish_api.check_api_version()

    if cmd_args.verbose:
        logger.info("verbose is set on")

    if cmd_args.subcommand not in command_name_to_cmd:
        console_error_printer("Error: Unknown command.")
        return

    try:
        cmd = command_name_to_cmd[cmd_args.subcommand]
        arg_dict = dict((k, v) for k, v
                        in vars(cmd_args).items() if k != "message_type")

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
                "idrac_ip": cmd_args.idrac_ip or "",
                "username": cmd_args.idrac_username or "",
                "password": cmd_args.idrac_password or "",
                "port": cmd_args.idrac_port,
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

        if command_result.error is not None:
            json_printer(command_result.data, cmd_args, colorized=cmd_args.nocolor)
            if isinstance(command_result.error, JsonHttpError):
                console_error_printer(command_result.error.json_error)
            return

        processed_data = process_respond(cmd_args, command_result)
        if json_printer:
            json_printer(processed_data, cmd_args, colorized=cmd_args.nocolor)

    except RedfishException as redfish_err:
        console_error_printer(f"Error: {redfish_err}")
    except TaskIdUnavailable as tid:
        console_error_printer(f"Error: {tid}")
    except MissingResource as mr:
        console_error_printer(f"Error: {mr}")
    except InvalidJsonSpec as ijs:
        console_error_printer(f"Error: {ijs}")
    except ResourceNotFound as rnf:
        console_error_printer(f"Error: {rnf}")
    except InvalidArgument as ia:
        console_error_printer(f"Error: {ia}")
    except FailedDiscoverAction as fda:
        console_error_printer(f"Error: {fda}")
    except UnsupportedAction as ua:
        console_error_printer(f"Error:{ua}")
    except MissingMandatoryArguments as mmr:
        console_error_printer(f"Error:{mmr}")
    except FileNotFoundError as fne:
        console_error_printer(f"Error:{fne}")
    except UncommittedPendingChanges as upc:
        console_error_printer(f"Error:{upc}")


def create_cmd_tree(arg_parser, debug=False) -> Dict:
    """Create command tree structure.
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

    credentials = parser.add_argument_group('credentials', '# idrac credentials details.')

    # global args. Endpoint/credentials read the going-forward REDFISH_* env vars
    # first, falling back to the legacy IDRAC_* names (both work during the rename).
    credentials.add_argument(
        '--idrac_ip', required=False, type=str,
        default=_env('REDFISH_IP', 'IDRAC_IP'),
        help="BMC ip address, by default "
             "read from environment REDFISH_IP (or legacy IDRAC_IP).")
    credentials.add_argument(
        '--idrac_username', required=False, type=str,
        default=_env('REDFISH_USERNAME', 'IDRAC_USERNAME', default='root'),
        help="BMC username, by default "
             "read from environment REDFISH_USERNAME (or legacy IDRAC_USERNAME).")
    credentials.add_argument(
        '--idrac_password', required=False, type=str,
        default=_env('REDFISH_PASSWORD', 'IDRAC_PASSWORD'),
        help="BMC password, by default "
             "read from environment REDFISH_PASSWORD (or legacy IDRAC_PASSWORD).")
    credentials.add_argument(
        '--idrac_port', required=False, type=int,
        default=int(_env('REDFISH_PORT', 'IDRAC_PORT', default='443')),
        help="BMC port, by default "
             "read from environment REDFISH_PORT (or legacy IDRAC_PORT).")
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
    if args.debug:
        logger.setLevel(args.log)

    if getattr(args, "subcommand", None) == "exporter":
        if exporter_argv_uses_secret(sys.argv):
            print(
                "Please provide exporter credentials through environment "
                "variables or --credential-file, not --idrac_password."
            )
            sys.exit(2)
        try:
            apply_exporter_env_file(args)
        except FileNotFoundError as fne:
            console_error_printer(f"Error:{fne}")
            sys.exit(1)

    # A network-segment scan (bmc-scan, or discovery --network) has no single
    # target host and uses no credentials, so it skips the IDRAC_IP/credential
    # gate that every host-targeted command requires.
    if (
        not is_network_scan(args)
        and not is_local_command(args)
        and not is_fleet_command(args)
    ):
        if args.idrac_ip is None or len(args.idrac_ip) == 0:
            print(
                "Please indicate the idrac ip. "
                "--idrac_ip or set IDRAC_IP environment variable. "
                "(export IDRAC_IP=ip_address)"
            )
            sys.exit(1)
        if args.idrac_username is None or len(args.idrac_username) == 0:
            print(
                "Please indicate the idrac username."
                "--idrac_username or set IDRAC_USERNAME environment variable. "
                "(export IDRAC_USERNAME=ip_address)"
            )
            sys.exit(1)
        if args.idrac_password is None or len(args.idrac_password) == 0:
            print(
                "Please indicate the idrac password. "
                "--idrac_password or set IDRAC_PASSWORD environment."
                "(export IDRAC_PASSWORD=ip_address)"
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
