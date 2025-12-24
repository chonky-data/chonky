import os
import sys

from argparse import ArgumentParser
from pathlib import Path
from typing import Generator

from chonky import Client, ClientError, get_cache_path


def locate_configs(
    workspace_root: Path = Path.cwd(), config_name: str = "CHONKY"
) -> Generator[Path, None, None]:
    for root, dirs, files in os.walk(workspace_root):
        dirs[:] = [d for d in dirs if not d.startswith((".", "__"))]
        if config_name in files:
            yield Path(root).relative_to(workspace_root) / config_name


def stats() -> None:
    cache_path = get_cache_path()
    total_bytes = 0
    total_files = 0
    for entry in cache_path.iterdir():
        if entry.is_file():
            total_files += 1
            total_bytes += entry.stat().st_size
    print(f"Local cache path: {cache_path}")
    print(f"Objects: {total_files}")
    print(f"Total size: {total_bytes:,} bytes")


WORKSPACE_COMMANDS = {
    "status": Client.status,
    "sync": Client.sync,
    "submit": Client.submit,
    "revert": Client.revert,
}

GLOBAL_COMMANDS = {
    "stats": stats,
}


def main() -> None:
    all_commands = list(WORKSPACE_COMMANDS.keys()) + list(GLOBAL_COMMANDS.keys())
    parser = ArgumentParser()
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("command", choices=all_commands)
    args = parser.parse_args()

    if args.command in GLOBAL_COMMANDS:
        GLOBAL_COMMANDS[args.command]()
        return

    configs = [args.config] if args.config else list(locate_configs())
    if not configs:
        print("No workspaces found", file=sys.stderr)
    try:
        command = WORKSPACE_COMMANDS[args.command]
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
