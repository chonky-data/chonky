from pathlib import Path
from urllib.parse import urlparse

from chonky.base_remote import BaseRemote
from chonky.s3_remote import S3Remote


def make_remote(remote_uri: str, local_root: Path) -> BaseRemote:
    uri = urlparse(remote_uri)
    remote_root = Path(uri.path.strip("/"))
    if uri.scheme == "s3":
        return S3Remote(
            remote_host=uri.netloc, remote_root=remote_root, local_root=local_root
        )
    else:
        raise ValueError(f"Unsupported remote URI scheme: {uri}")
