import threading
from multiprocessing.dummy import Pool as ThreadPool
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError
from tqdm import tqdm

from chonky.base_remote import BaseRemote

# Files transferred in parallel; the size of the push/pull worker pool.
MAX_CONCURRENT_FILES = 4
# Parts of a single file transferred at once (TransferConfig.max_concurrency).
CONCURRENT_PARTS_PER_FILE = 8

CLIENT_CONFIG = Config(
    connect_timeout=60,
    read_timeout=300,
    retries={"max_attempts": 5, "mode": "standard"},
    # Every request runs on a transfer worker thread, bounded at
    # CONCURRENT_PARTS_PER_FILE per file across MAX_CONCURRENT_FILES files: the exact
    # ceiling of live connections.
    max_pool_connections=MAX_CONCURRENT_FILES * CONCURRENT_PARTS_PER_FILE,
    request_checksum_calculation="when_required", # avoid per-part CRC reducing callback frequency
)

TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=16 * 1024 * 1024,
    multipart_chunksize=16 * 1024 * 1024,
    max_concurrency=CONCURRENT_PARTS_PER_FILE,
)


class _ProgressCallback:
    # boto3 invokes the transfer callback from multiple part threads at once, and
    # tqdm.update is not thread-safe, so serialize updates with a lock.
    def __init__(self, pbar: tqdm):
        self._pbar = pbar
        self._lock = threading.Lock()

    def __call__(self, bytes_amount: int) -> None:
        with self._lock:
            self._pbar.update(bytes_amount)


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

    def _client(self):
        session = boto3.session.Session()
        return session.client("s3", endpoint_url=self.endpoint, config=CLIENT_CONFIG)

    def pull(self, keys: list[str]) -> None:
        client = self._client()

        with tqdm(
            desc="Pulling",
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
        ) as pbar:
            callback = _ProgressCallback(pbar)

            def fetch(key: str) -> None:
                client.download_file(
                    Bucket=self.config.bucket,
                    Key=str(self.remote_root.joinpath(key)),
                    Filename=str(self.local_root.joinpath(key)),
                    Config=TRANSFER_CONFIG,
                    Callback=callback,
                )

            with ThreadPool(MAX_CONCURRENT_FILES) as pool:
                for _ in pool.imap_unordered(fetch, keys):
                    pass

    def push(self, keys: list[str]) -> None:
        client = self._client()

        def does_key_exist(remote_key: str) -> bool:
            try:
                client.head_object(Bucket=self.config.bucket, Key=remote_key)
                return True
            except ClientError as e:
                if e.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                    raise
                return False

        def to_upload(key: str):
            remote_key = str(self.remote_root.joinpath(key))
            if does_key_exist(remote_key):
                return None
            local_path = self.local_root.joinpath(key)
            return (str(local_path), remote_key, local_path.stat().st_size)

        with ThreadPool(MAX_CONCURRENT_FILES) as pool:
            pending = [item for item in pool.imap_unordered(to_upload, keys) if item]

        total_bytes = sum(size for _, _, size in pending)

        with tqdm(
            total=total_bytes,
            desc="Pushing",
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
        ) as pbar:
            callback = _ProgressCallback(pbar)

            def upload(item) -> None:
                local_path, remote_key, _ = item
                client.upload_file(
                    local_path,
                    self.config.bucket,
                    remote_key,
                    Config=TRANSFER_CONFIG,
                    Callback=callback,
                )

            with ThreadPool(MAX_CONCURRENT_FILES) as pool:
                for _ in pool.imap_unordered(upload, pending):
                    pass
