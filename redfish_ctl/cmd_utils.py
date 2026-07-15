"""
Command line utils functions.  All utility function
should be here.

Author Mus spyroot@gmail.com
"""
import argparse
import os
import warnings
from pathlib import Path
from typing import Optional
import json


def from_json_spec(from_spec: str):
    """read json from a file
    :param from_spec: a path to a file with json spec.
    :return: the parsed JSON payload.
    """
    with open(from_spec) as user_file:
        file_contents = user_file.read()
    payload = json.loads(file_contents)
    return payload


def _find_ids(obj, key, result):
    """Recursively collect values stored under ``key`` into ``result``.

    :param obj: dict, list, or scalar value to search.
    :param key: key whose values are collected.
    :param result: accumulator list that matching values are appended to.
    """
    if obj is None:
        return

    if isinstance(obj, dict):
        for k in obj.keys():
            if isinstance(obj[k], dict):
                _find_ids(obj[k], key, result)
            if isinstance(obj[k], list):
                for e in obj[k]:
                    _find_ids(e, key, result)
            else:
                if k == key:
                    result.append(obj[k])


def find_ids(obj, key):
    """Recursively search key in object
    :param obj:
    :param key:
    :return:
    """
    result = []
    _find_ids(obj, key, result)
    return result


def str2bool(v):
    """Convert a string (or bool) to a boolean.

    :param v: value to interpret; a bool passes through, while a string such as
        ``yes``/``true``/``t``/``y``/``1`` maps to True and
        ``no``/``false``/``f``/``n``/``0`` maps to False.
    :return: the parsed boolean value.
    :raise argparse.ArgumentTypeError: if the string is not a recognized boolean.
    """
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


def save_if_needed(filename: str, raw_data,
                   data_format: Optional[str] = "json",
                   indents: Optional[int] = 4,
                   save_dir: Optional[str] = None) -> None:
    """Save json raw data to a file.

    if filename already full path function will use that
    if caller provider both full path and save_dir arg
    save_dir will take precedence.

    Note function will add prefix a file either json or yaml.

    if caller provided save_dir optional arg function will
    create a dir.

    :param indents: default json ident relevant for a json only
    :param filename: a filename
    :param raw_data: a raw json string
    :param data_format: data format we save.  json, yaml, xml etc.
    :param save_dir: a directory where we need to save, if not exists will create.
    :return: Nothing
    :raise ValueError if invalid data format passed.
    """
    if filename is None or len(filename) == 0:
        return

    if raw_data is None:
        return

    target_dir = None
    if save_dir is not None and len(save_dir) > 0:
        save_dir_path = Path(save_dir).expanduser().resolve()
        if save_dir_path.exists() and save_dir_path.is_dir():
            target_dir = str(save_dir_path)
        if not save_dir_path.exists():
            os.makedirs(str(save_dir_path), exist_ok=True)
        if not save_dir_path.is_dir():
            raise ValueError("Save dir is file not a directory.")

    file_path = Path(filename).expanduser().resolve()
    if file_path.is_dir():
        warnings.warn("Can't save a result it to a file. "
                      "Please provide a filename, "
                      "not a path to a directory.")
        return

    if target_dir is not None:
        final_filename = target_dir + str(file_path.stem)
    else:
        final_filename = str(file_path)

    if data_format == "json":
        import json
        if '.json' in final_filename:
            final_filename = f"{final_filename}"
        else:
            final_filename = f"{final_filename}.json"
        with open(final_filename, 'w') as f:
            json.dump(raw_data, f, indent=indents)
    elif data_format == "yaml":
        import yaml
        if '.yaml' in final_filename:
            final_filename = f"{final_filename}"
        else:
            final_filename = f"{final_filename}.yaml"
        with open(final_filename, 'w') as file:
            yaml.dump(raw_data, file)
    else:
        raise ValueError(f"Unknown data format '{data_format}'.")
