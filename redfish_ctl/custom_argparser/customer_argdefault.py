"""overwrites some argparse defaults methods,
mainly for formatting, color etc.

Author Mus spyroot@gmail.com
"""
import argparse
import os
import string
import textwrap as _textwrap
from typing import Optional


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


def make_color_string(msg: str, tcolor: Optional[str] = TermColors.OK_GREEN):
    """Printer error and if terminal support color print red or green etc.

    :param msg: message whose bracketed and braced tokens are colorized.
    :param tcolor: color code applied to text inside square brackets.
    :return: the message with color escape codes when the terminal supports
        color, otherwise the original message unchanged.
    """
    current_term = os.getenv("TERM")
    if current_term is not None and current_term in TermList:
        msg = msg.replace("[", "[" + tcolor)
        msg = msg.replace("]", "]" + TermColors.ENDC)

        msg = msg.replace("{", "{" + TermColors.OK_BLUE)
        msg = msg.replace("}", "}" + TermColors.ENDC)

        return msg
    else:
        return msg


def insertChar(s: str, pos: int, ch):
    """Insert a character or substring into a string at a given position.

    :param s: source string.
    :param pos: index at which to insert.
    :param ch: character or substring to insert.
    :return: new string with ``ch`` inserted at ``pos``.
    """
    return s[:pos] + ch + s[pos:]


def make_action_colorful(msg: str, tcolor: Optional[str] = TermColors.OK_GREEN):
    """Printer error and if terminal support color print red or green etc.

    :param msg: message whose braces and first ``--`` option are colorized.
    :param tcolor: accepted for signature parity with make_color_string; not used
        by this function, which applies fixed colors.
    :return: the message with color escape codes when the terminal supports
        color, otherwise the original message unchanged.
    """
    current_term = os.getenv("TERM")

    if current_term is not None and current_term in TermList:
        msg = msg.replace("{", "{" + TermColors.OK_BLUE)
        msg = msg.replace("}", "}" + TermColors.ENDC)

        tokens = list(msg.partition("--"))
        for i, t in enumerate(tokens):
            if t == "--":
                tokens[i] = TermColors.OK_GREEN + tokens[i]
                tokens[i] = tokens[i].strip()
                next_token = tokens[i + 1]
                space_token = 0
                for j, ch in enumerate(next_token):
                    if ch in string.whitespace:
                        space_token = j
                        break
                if space_token != 0:
                    tokens[i + 1] = insertChar(next_token, space_token, TermColors.ENDC)
                break

        msg = ''.join(tokens)
    return msg


class BiosSubcommand(argparse.ArgumentParser):
    def __init__(self, **kwargs):
        """Initialize the BiosSubcommand argument parser."""
        super(BiosSubcommand, self).__init__(**kwargs)

    def format_help(self):
        """Return the formatted help text for this parser.

        :return: the formatted help string from the base ArgumentParser.
        """
        return super().format_help()


class CustomArgumentDefaultsHelpFormatter(argparse.HelpFormatter):
    """Help message formatter which adds default values to argument help.
    Only the name of this class is considered a public API. All the methods
    provided by the class are considered an implementation detail.
    """

    def format_help(self):
        """Return the formatted help text.

        :return: the formatted help string from the base formatter.
        """
        f = super().format_help()
        return f

    def _get_help_string(self, action):
        """Return the help string for an action, appending its default value.

        :param action: the argparse action whose help string is built.
        :return: the help text, with a ``(default: ...)`` suffix added when the
            action has a default that is neither suppressed nor already shown.
        """
        help_msg = action.help
        if '%(default)' not in action.help:
            if action.default is not argparse.SUPPRESS:
                defaulting_nargs = [argparse.OPTIONAL, argparse.ZERO_OR_MORE]
                if action.option_strings or action.nargs in defaulting_nargs:
                    help_msg += ' (default: %(default)s)'
        return help_msg

    # def add_argument(self, action):
    #     # super(self).add_argument(action)
    #     print("Passed up actions", action)
    #     self.add_argument(action)

    def _split_lines(self, text, width):
        """Split help text into lines, honoring an ``R|`` raw-text prefix.

        :param text: the help text to wrap.
        :param width: the maximum line width.
        :return: list of lines; raw splitlines when ``text`` begins with ``R|``,
            otherwise the default wrapped lines.
        """
        if text.startswith('R|'):
            return text[2:].splitlines()
            # this is the RawTextHelpFormatter._split_lines
        return argparse.HelpFormatter._split_lines(self, text, width)

    def _iter_indented_subactions(self, action):
        """Sort and ident command
        :param action:
        :return:
        """
        try:
            get_subactions = action._get_subactions
        except AttributeError:
            pass
        else:
            self._indent()
            if isinstance(action, argparse._SubParsersAction):
                for subaction in sorted(get_subactions(), key=lambda x: x.dest):
                    yield subaction
            else:
                for subaction in get_subactions():
                    yield subaction
            self._dedent()

    def _fill_text(self, text, width, indent):
        """
        :param text:
        :param width:
        :param indent:
        :return:
        """
        text = self._whitespace_matcher.sub(' ', text).strip()
        paragraphs = text.split('|n ')
        multiline_text = ''
        for paragraph in paragraphs:
            formatted_paragraph = _textwrap.fill(
                paragraph, width,
                initial_indent=indent,
                subsequent_indent=indent) + '\n'
            multiline_text = multiline_text + formatted_paragraph
        return multiline_text

    def _format_actions_usage(self, actions, groups):
        """Format the usage string for actions and colorize it.

        :param actions: the actions to include in the usage string.
        :param groups: the mutually exclusive groups to render.
        :return: the colorized usage string.
        """
        text = super()._format_actions_usage(actions, groups)
        # return the text
        return make_color_string(text)

    def _format_action(self, action):
        """Format a single action for the help output and colorize it.

        :param action: the action to format.
        :return: the colorized formatted action string.
        """
        s = super()._format_action(action)
        return make_action_colorful(s)

    def _iter_indented_subactions(self, action):
        """Iterate a subparser action's sub-actions with increased indentation.

        :param action: the action whose sub-actions are iterated.
        """
        try:
            get_subactions = action._get_subactions
        except AttributeError:
            pass
        else:
            self._indent()
            if isinstance(action, argparse._SubParsersAction):
                for sub_action in sorted(get_subactions(), key=lambda x: x.dest):
                    yield sub_action
            else:
                for sub_action in get_subactions():
                    yield sub_action
            self._dedent()

    # def _iter_indented_subactions(self, action):
    #     try:
    #         get_subactions = action._get_subactions
    #     except AttributeError:
    #         pass
    #     else:
    #         self._indent()
    #         yield from get_subactions()
    #         self._dedent()

    # def _iter_indented_subactions(self, action):
    #     try:
    #         get_subactions = action._get_subactions
    #     except AttributeError:
    #         pass
    #     else:
    #         self._indent()
    #         if isinstance(action, argparse._SubParsersAction):
    #             for subaction in sorted(get_subactions(), key=lambda x: x.dest):
    #                 yield subaction
    #         else:
    #             for subaction in get_subactions():
    #                 yield subaction
    #         self._dedent()
