import socket
import tempfile
from pathlib import Path
from typing import Any, Generator, NoReturn
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError as BotoClientError
from moto import mock_aws

import chonky.client
from chonky import Client, ClientError
from chonky.client import HashFile, LoadConfig, WriteConfig
from chonky.s3_remote import S3Remote


def _blocked_socket(*args: Any, **kwargs: Any) -> NoReturn:
    raise RuntimeError("Network access is blocked during tests")


# Block network access for all tests
socket.socket = _blocked_socket  # type: ignore[misc,assignment]


# Patched S3Remote.pull - creates boto3 client without endpoint_url so moto can intercept
def _patched_pull(self: S3Remote, keys: list[str]) -> None:
    client = boto3.client("s3", region_name="us-east-1")
    for key in keys:
        client.download_file(
            Bucket=self.config.bucket,
            Key=str(self.remote_root.joinpath(key)),
            Filename=str(self.local_root.joinpath(key)),
        )


# Patched S3Remote.push - creates boto3 client without endpoint_url so moto can intercept
def _patched_push(self: S3Remote, keys: list[str]) -> None:
    client = boto3.client("s3", region_name="us-east-1")

    def does_key_exist(remote_key: str) -> bool:
        try:
            client.head_object(Bucket=self.config.bucket, Key=remote_key)
            return True
        except BotoClientError as e:
            if e.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                raise
            return False

    for key in keys:
        remote_key = str(self.remote_root.joinpath(key))
        if not does_key_exist(remote_key):
            client.upload_file(
                str(self.local_root.joinpath(key)), self.config.bucket, remote_key
            )


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def cache_dir(temp_dir: Path) -> Generator[Path, None, None]:
    cache = temp_dir / "cache"
    cache.mkdir()
    with patch.object(chonky.client, "get_cache_path", lambda: cache):
        yield cache


@pytest.fixture
def mock_s3_env(cache_dir: Path) -> Generator[None, None, None]:
    with mock_aws():
        with patch.object(S3Remote, "pull", _patched_pull), patch.object(
            S3Remote, "push", _patched_push
        ):
            yield


@pytest.fixture
def mock_s3_bucket(mock_s3_env: None) -> str:
    bucket_name = "test-chonky-bucket"
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket_name)
    return bucket_name


@pytest.fixture
def chonky_repo(temp_dir: Path, mock_s3_bucket: str) -> tuple[Path, Path]:
    repo_root = temp_dir / "test_repo"
    repo_root.mkdir()

    (repo_root / "CHONKY").write_text(
        f"""[config]
type = s3
bucket = {mock_s3_bucket}
endpoint = http://localhost:5000
workspace = Assets/

[HEAD]
"""
    )

    workspace = repo_root / "Assets"
    workspace.mkdir()
    return repo_root, workspace


def test_initial_status(
    chonky_repo: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root, _ = chonky_repo
    client = Client(repo_root / "CHONKY")

    client.status()
    output = capsys.readouterr().out

    assert "Workspace is up to date with the remote" in output
    assert "Workspace has no changes to submit" in output


def test_submit_new_file(chonky_repo: tuple[Path, Path]) -> None:
    repo_root, workspace = chonky_repo
    client = Client(repo_root / "CHONKY")

    test_file = workspace / "test.txt"
    test_file.write_text("Hello, Chonky!")
    client.submit()

    config = LoadConfig(repo_root / "CHONKY")
    assert "test.txt" in config["HEAD"]
    assert config["HEAD"]["test.txt"] == HashFile(test_file)
    assert test_file.read_text() == "Hello, Chonky!"


def test_sync_from_remote(chonky_repo: tuple[Path, Path]) -> None:
    repo_root, workspace = chonky_repo
    client = Client(repo_root / "CHONKY")

    # Submit a file
    test_file = workspace / "remote_file.txt"
    test_file.write_text("From remote")
    client.submit()

    # Simulate another client: remove file locally and clear local HEAD
    test_file.unlink()
    local_config = LoadConfig(workspace / ".HEAD")
    del local_config["HEAD"]["remote_file.txt"]
    WriteConfig(local_config, workspace / ".HEAD")

    # Sync should restore the file
    Client(repo_root / "CHONKY").sync()

    assert test_file.exists()
    assert test_file.read_text() == "From remote"


def test_status_with_changes(
    chonky_repo: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root, workspace = chonky_repo
    client = Client(repo_root / "CHONKY")

    test_file = workspace / "status_test.txt"
    test_file.write_text("Initial")
    client.submit()
    test_file.write_text("Modified")

    client.status()
    output = capsys.readouterr().out

    assert "modified" in output.lower() or "status_test.txt" in output


def test_revert_changes(chonky_repo: tuple[Path, Path]) -> None:
    repo_root, workspace = chonky_repo
    client = Client(repo_root / "CHONKY")

    test_file = workspace / "revert_test.txt"
    test_file.write_text("Original")
    client.submit()
    test_file.write_text("Modified")

    client.revert()

    assert test_file.read_text() == "Original"


def test_submit_multiple_files(chonky_repo: tuple[Path, Path]) -> None:
    repo_root, workspace = chonky_repo
    client = Client(repo_root / "CHONKY")

    files = {
        "file1.txt": "Content 1",
        "file2.txt": "Content 2",
        "subdir/file3.txt": "Content 3",
    }
    for path, content in files.items():
        f = workspace / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)

    client.submit()

    config = LoadConfig(repo_root / "CHONKY")
    for path in files:
        assert path in config["HEAD"]
        assert config["HEAD"][path] == HashFile(workspace / path)


def test_delete_file(chonky_repo: tuple[Path, Path]) -> None:
    repo_root, workspace = chonky_repo
    client = Client(repo_root / "CHONKY")

    test_file = workspace / "to_delete.txt"
    test_file.write_text("Delete me")
    client.submit()
    test_file.unlink()
    client.submit()

    config = LoadConfig(repo_root / "CHONKY")
    assert "to_delete.txt" not in config["HEAD"]


def test_conflict_detection(chonky_repo: tuple[Path, Path]) -> None:
    repo_root, workspace = chonky_repo
    client = Client(repo_root / "CHONKY")

    test_file = workspace / "conflict_test.txt"
    test_file.write_text("Initial")
    client.submit()

    # Simulate remote change
    config = LoadConfig(repo_root / "CHONKY")
    config["HEAD"]["conflict_test.txt"] = "fake_remote_hash"
    WriteConfig(config, repo_root / "CHONKY")

    # Modify locally
    test_file.write_text("Local change")

    # Sync should raise conflict
    with pytest.raises(ClientError, match="[Cc]onflict"):
        Client(repo_root / "CHONKY").sync()


def test_cache_integrity(chonky_repo: tuple[Path, Path], cache_dir: Path) -> None:
    repo_root, workspace = chonky_repo
    client = Client(repo_root / "CHONKY")

    # Submit multiple files
    files = {"a.txt": "Content A", "b.txt": "Content B", "c.txt": "Content C"}
    for path, content in files.items():
        (workspace / path).write_text(content)
    client.submit()

    # Verify each cached file's name matches its SHA1 hash
    cached_files = list(cache_dir.iterdir())
    assert len(cached_files) == 3

    for cached_file in cached_files:
        assert cached_file.name == HashFile(cached_file)
