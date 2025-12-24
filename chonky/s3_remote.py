from multiprocessing.dummy import Pool as ThreadPool
from pathlib import Path

import boto3  # type: ignore [import-untyped]
from botocore.exceptions import ClientError  # type: ignore [import-untyped]
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
        session = boto3.session.Session()
        client = session.client("s3", endpoint_url=self.endpoint)

        def does_key_exist(remote_key: str) -> bool:
            try:
                client.head_object(Bucket=self.config.bucket, Key=remote_key)
                return True
            except ClientError as e:
                if e.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                    raise
                return False

        def upload(key: str) -> None:
            remote_key = str(self.remote_root.joinpath(key))
            if not does_key_exist(remote_key):
                local_path = str(self.local_root.joinpath(key))
                client.upload_file(local_path, self.config.bucket, remote_key)

        with ThreadPool() as pool:
            for _ in tqdm(
                pool.imap_unordered(upload, keys),
                desc="Pushing",
                unit="files",
                total=len(keys),
            ):
                pass
