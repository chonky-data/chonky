import sys

from argparse import ArgumentParser
from pathlib import Path

from chonky import Client, ClientError

def main():
    COMMANDS = {
        "status":   Client.status,
        "sync":     Client.sync,
        "submit":   Client.submit,
        "revert":   Client.revert,
    }
    parser = ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path.cwd() / "CHONKY")
    parser.add_argument("command", choices=COMMANDS.keys())
    args = parser.parse_args()
    try:
        client = Client(args.config)
        COMMANDS[args.command](client)
    except ClientError as e:
        print(e, file=sys.stderr)
        quit(1)

if __name__ == "__main__":
    main()