from __future__ import annotations

import threading
import time
from multiprocessing.dummy import Pool as ThreadPool
from pathlib import Path
from typing import NoReturn

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from tqdm import tqdm

from chonky.base_remote import BaseRemote

# Up to MAX_CONCURRENT_FILES files transfer at once, each split into
# CONCURRENT_PARTS_PER_FILE parallel parts; the product is the live connection ceiling.
MAX_CONCURRENT_FILES = 2
CONCURRENT_PARTS_PER_FILE = 2

CLIENT_CONFIG = Config(
    # The body upload (write) is bounded by connect_timeout, not read_timeout, so both
    # are raised. It is a per-stall limit (no progress for the interval), not
    # per-transfer, so the value is independent of file size.
    connect_timeout=300,
    read_timeout=300,
    retries={"max_attempts": 5, "mode": "standard"},
    max_pool_connections=MAX_CONCURRENT_FILES * CONCURRENT_PARTS_PER_FILE,
    # The default per-part CRC wraps the body in aws-chunked encoding, which coarsens
    # the byte progress callbacks; when_required keeps them fine-grained.
    request_checksum_calculation="when_required",
)

TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=16 * 1024 * 1024,
    multipart_chunksize=16 * 1024 * 1024,
    max_concurrency=CONCURRENT_PARTS_PER_FILE,
)


class _ByteProgress:
    # upload_file invokes the callback from multiple part threads at once, and
    # tqdm.update is not thread-safe, so serialize updates with a lock.
    def __init__(self, pbar: tqdm[NoReturn]):
        self._pbar = pbar
        self._lock = threading.Lock()

    def __call__(self, bytes_amount: int) -> None:
        with self._lock:
            self._pbar.update(bytes_amount)


class _PullProgress:
    # The bar counts completed objects; bytes from download_file's callback drive a
    # recent-window MB/s readout in the postfix. Both touch the bar from different
    # threads, so one lock guards every bar mutation.
    def __init__(self, pbar: tqdm[NoReturn]):
        self._pbar = pbar
        self._lock = threading.Lock()
        self._window_bytes = 0
        self._last = time.monotonic()

    def on_bytes(self, bytes_amount: int) -> None:
        with self._lock:
            self._window_bytes += bytes_amount
            now = time.monotonic()
            interval = now - self._last
            if interval >= 0.5:
                rate = self._window_bytes / interval / 1e6
                self._pbar.set_postfix_str(f"{rate:.1f} MB/s", refresh=True)
                self._window_bytes = 0
                self._last = now

    def on_object_done(self) -> None:
        with self._lock:
            self._pbar.update(1)


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
        client = boto3.session.Session().client(
            "s3", endpoint_url=self.endpoint, config=CLIENT_CONFIG
        )

        with tqdm(total=len(keys), desc="Pulling", unit="obj") as pbar:
            progress = _PullProgress(pbar)

            def fetch(key: str) -> None:
                client.download_file(
                    Bucket=self.config.bucket,
                    Key=str(self.remote_root.joinpath(key)),
                    Filename=str(self.local_root.joinpath(key)),
                    Config=TRANSFER_CONFIG,
                    Callback=progress.on_bytes,
                )

            with ThreadPool(MAX_CONCURRENT_FILES) as pool:
                for _ in pool.imap_unordered(fetch, keys):
                    progress.on_object_done()

    def push(self, keys: list[str]) -> None:
        client = boto3.session.Session().client(
            "s3", endpoint_url=self.endpoint, config=CLIENT_CONFIG
        )

        # Content-addressed keys make re-uploading an existing blob a harmless
        # idempotent overwrite, so upload unconditionally rather than probing S3.
        # Byte total comes from local stat (no S3 request).
        total_bytes = sum(self.local_root.joinpath(key).stat().st_size for key in keys)

        with tqdm(
            total=total_bytes,
            desc="Pushing",
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
        ) as pbar:
            progress = _ByteProgress(pbar)

            def upload(key: str) -> None:
                client.upload_file(
                    str(self.local_root.joinpath(key)),
                    self.config.bucket,
                    str(self.remote_root.joinpath(key)),
                    Config=TRANSFER_CONFIG,
                    Callback=progress,
                )

            with ThreadPool(MAX_CONCURRENT_FILES) as pool:
                for _ in pool.imap_unordered(upload, keys):
                    pass
