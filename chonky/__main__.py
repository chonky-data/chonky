import os
import sys

from argparse import ArgumentParser
from pathlib import Path
from typing import Generator

from chonky import Client, ClientError


def locate_configs(
    workspace_root: Path = Path.cwd(), config_name: str = "CHONKY"
) -> Generator[Path, None, None]:
    for root, dirs, files in os.walk(workspace_root):
        dirs[:] = [d for d in dirs if not d.startswith((".", "__"))]
        if config_name in files:
            yield Path(root).relative_to(workspace_root) / config_name


def main() -> None:
    COMMANDS = {
        "status": Client.status,
        "sync": Client.sync,
        "submit": Client.submit,
        "revert": Client.revert,
    }
    parser = ArgumentParser()
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("command", choices=COMMANDS.keys())
    args = parser.parse_args()
    configs = [args.config] if args.config else list(locate_configs())
    if not configs:
        print("No workspaces found", file=sys.stderr)
    try:
        command = COMMANDS[args.command]
        for i, config in enumerate(configs):
            if i > 0:
                print("")  # line break between workspaces
            print(f"Workspace: {config}")
            client = Client(config)
            command(client)
    except ClientError as e:
        print(e, file=sys.stderr)
        quit(1)


if __name__ == "__main__":
    main()
