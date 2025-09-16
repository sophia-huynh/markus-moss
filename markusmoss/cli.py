import os
import mosspy
import toml
import argparse
from .markusmoss import MarkusMoss

DEFAULTRC = "markusmossrc"


DEFAULTS = {
    "workdir": os.getcwd(),
    "file_glob": "**/*",
}


def _parse_config(pre_args):
    args_dict = vars(pre_args).copy()
    if os.path.isfile(pre_args.config):
        with open(pre_args.config) as cf:
            config_args = toml.load(cf)
        for key, value in config_args.items():
            if args_dict.get(key) is None:
                args_dict[key] = value
    for key, value in DEFAULTS.items():
        if args_dict.get(key) is None:
            args_dict[key] = value
    args_dict.pop('config')
    return args_dict


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--markus-api-key")
    parser.add_argument("--markus-url")
    parser.add_argument("--markus-assignment")
    parser.add_argument("--markus-course")
    parser.add_argument("--moss-userid")
    parser.add_argument("--moss-report-url")
    parser.add_argument("--config", default=os.path.join(os.getcwd(), DEFAULTRC))
    parser.add_argument("--workdir")
    parser.add_argument("--actions", nargs="*", default=None, choices=MarkusMoss.ACTIONS)
    parser.add_argument("--language", choices=mosspy.Moss.languages)
    parser.add_argument("--file-glob")
    parser.add_argument("--groups", nargs="*", default=None)
    parser.add_argument("--generate-config", nargs='?', default=-1)
    parser.add_argument("-f", "--force", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-s", "--select", nargs='+',
                        help="A single match number, or a list of group names")

    return _parse_config(parser.parse_args())


def cli():
    kwargs = _parse_args()
    output = kwargs.pop("generate_config")
    if output != -1:
        if output is None:
            print(toml.dumps(kwargs))
        else:
            with open(output, 'w') as f:
                toml.dump(kwargs, f)
        return
    actions = kwargs.pop("actions")
    selected_groups = kwargs.pop("selected_groups")
    MarkusMoss(**kwargs).run(actions=actions, selected_groups=selected_groups)


if __name__ == "__main__":
    cli()
