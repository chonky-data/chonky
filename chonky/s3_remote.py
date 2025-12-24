from multiprocessing.dummy import Pool as ThreadPool
from pathlib import Path

import boto3  # type: ignore [import-not-found]
from tqdm import tqdm  # type: ignore [import-untyped]

from chonky.base_remote import BaseRemote


class S3Remote(BaseRemote):
    @property
    def endpoint(self) -> str:
        endpoint = self.config.endpoint
        if not endpoint.startswith(("http://", "https://")):
            return f"https://{endpoint}"
        return endpoint

    @property
    def remote_root(self) -> Path:
        return Path(self.config.root) if self.config.root else Path("")

    def pull(self, keys: list[str]) -> None:
        session = boto3.session.Session()
        client = session.client("s3", endpoint_url=self.endpoint)

        def fetch(key: str) -> None:
            client.download_file(
                Bucket=self.config.bucket,
                Key=str(self.remote_root.joinpath(key)),
                Filename=str(self.local_root.joinpath(key)),
            )

        with ThreadPool() as pool:
            for _ in tqdm(
                pool.imap_unordered(fetch, keys),
                desc="Pulling",
                unit="files",
                total=len(keys),
            ):
                pass

    def push(self, keys: list[str]) -> None:
        s3 = boto3.resource("s3", endpoint_url=self.endpoint)
        bucket = s3.Bucket(self.config.bucket)
        for key in tqdm(keys, desc="Pushing", unit="files"):
            remote_key = str(self.remote_root.joinpath(key))
            if not list(bucket.objects.filter(Prefix=remote_key)):
                local_path = self.local_root.joinpath(key)
                bucket.upload_file(local_path, remote_key)
