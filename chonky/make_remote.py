from pathlib import Path

from chonky.base_remote import BaseRemote, RemoteConfig
from chonky.s3_remote import S3Remote


def make_remote(config: RemoteConfig, local_root: Path) -> BaseRemote:
    if config.type == "s3":
        return S3Remote(config, local_root)
    else:
        raise ValueError(f"Unsupported remote type: {config.type}")
