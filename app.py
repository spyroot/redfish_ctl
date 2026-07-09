import importlib
import sys
from idrac_ctl import redfish_main_ctl


def main():
    try:
        redfish_main_ctl()
    except ModuleNotFoundError:
        print('Invalid command')
        sys.exit(1)
