import socket
import tempfile
from pathlib import Path
from typing import Any, Generator, NoReturn
from unittest.mock import PropertyMock, patch

import boto3
import pytest
from moto import mock_aws

import chonky.client
from chonky import Client, ClientError
from chonky.client import HashFile, LoadConfig, WriteConfig
from chonky.s3_remote import S3Remote


def _blocked_socket(*args: Any, **kwargs: Any) -> NoReturn:
    raise RuntimeError("Network access is blocked during tests")


# Block network access for all tests
socket.socket = _blocked_socket  # type: ignore[misc,assignment]


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
        # Patch endpoint to None so boto3 uses default AWS endpoints that moto intercepts
        with patch.object(
            S3Remote, "endpoint", new_callable=PropertyMock, return_value=None
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


def test_status_output(
    chonky_repo: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root, workspace = chonky_repo
    client = Client(repo_root / "CHONKY")

    # Initial status - up to date
    client.status()
    output = capsys.readouterr().out
    assert "Workspace is up to date with the remote" in output
    assert "Workspace has no changes to submit" in output

    # Status with local changes
    test_file = workspace / "modified.txt"
    test_file.write_text("Initial")
    client.submit()
    test_file.write_text("Modified")
    client.status()
    output = capsys.readouterr().out
    assert "modified" in output.lower() or "modified.txt" in output

    # Status with missing file
    test_file.unlink()
    client.status()
    output = capsys.readouterr().out
    assert "missing" in output.lower() or "modified.txt" in output

    # Status with remote changes
    test_file.write_text("Content")
    client.submit()
    config = LoadConfig(repo_root / "CHONKY")
    config["HEAD"]["remote_file.txt"] = "fake_hash"
    WriteConfig(config, repo_root / "CHONKY")
    client.status()
    output = capsys.readouterr().out
    assert "remote" in output.lower() or "sync" in output.lower()

    # Status with added file
    added_file = workspace / "new_file.txt"
    added_file.write_text("New content")
    client.status()
    output = capsys.readouterr().out
    assert "added" in output.lower() or "new_file.txt" in output

    # Status with conflicts (exact message format)
    conflict_file = workspace / "conflict.txt"
    conflict_file.write_text("Initial")
    client.submit()
    config = LoadConfig(repo_root / "CHONKY")
    original_hash = config["HEAD"]["conflict.txt"]
    config["HEAD"]["conflict.txt"] = "remote_hash"
    WriteConfig(config, repo_root / "CHONKY")
    conflict_file.write_text("Local change")
    Client(repo_root / "CHONKY").status()
    output = capsys.readouterr().out
    assert "Conflicts must be resolved" in output
    assert "conflict.txt" in output

    # Resolve conflict by reverting local change
    conflict_file.write_text("Initial")
    config = LoadConfig(repo_root / "CHONKY")
    config["HEAD"]["conflict.txt"] = original_hash
    WriteConfig(config, repo_root / "CHONKY")

    # Status with remote changes showing added and missing
    config = LoadConfig(repo_root / "CHONKY")
    config["HEAD"]["remote_added.txt"] = "fake_hash"
    del config["HEAD"]["conflict.txt"]
    WriteConfig(config, repo_root / "CHONKY")
    local_config = LoadConfig(workspace / ".HEAD")
    del local_config["HEAD"]["conflict.txt"]
    WriteConfig(local_config, workspace / ".HEAD")
    conflict_file.unlink()
    Client(repo_root / "CHONKY").status()
    output = capsys.readouterr().out
    assert "Remote changes are available" in output
    assert "added" in output.lower() or "remote_added.txt" in output


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


def test_sync_operations(chonky_repo: tuple[Path, Path], cache_dir: Path) -> None:
    repo_root, workspace = chonky_repo

    # Sync with no remote changes (early return)
    test_file = workspace / "test.txt"
    test_file.write_text("Content")
    Client(repo_root / "CHONKY").submit()
    Client(repo_root / "CHONKY").sync()
    assert test_file.exists()
    assert test_file.read_text() == "Content"

    # Sync restores file from cache
    remote_file = workspace / "remote_file.txt"
    remote_file.write_text("From remote")
    Client(repo_root / "CHONKY").submit()
    remote_file.unlink()
    local_config = LoadConfig(workspace / ".HEAD")
    del local_config["HEAD"]["remote_file.txt"]
    WriteConfig(local_config, workspace / ".HEAD")
    Client(repo_root / "CHONKY").sync()
    assert remote_file.exists()
    assert remote_file.read_text() == "From remote"

    # Sync pulls from S3 when not in cache
    pull_file = workspace / "pull_test.txt"
    pull_file.write_text("Pull me from S3")
    file_hash = HashFile(pull_file)
    Client(repo_root / "CHONKY").submit()
    pull_file.unlink()
    local_config = LoadConfig(workspace / ".HEAD")
    del local_config["HEAD"]["pull_test.txt"]
    WriteConfig(local_config, workspace / ".HEAD")
    (cache_dir / file_hash).unlink()
    Client(repo_root / "CHONKY").sync()
    assert pull_file.exists()
    assert pull_file.read_text() == "Pull me from S3"
    assert (cache_dir / file_hash).exists()

    # Sync with remote modified file
    modified_file = workspace / "remote_modified.txt"
    modified_file.write_text("Original")
    Client(repo_root / "CHONKY").submit()
    config = LoadConfig(repo_root / "CHONKY")
    original_hash = config["HEAD"]["remote_modified.txt"]
    config["HEAD"]["remote_modified.txt"] = "new_remote_hash"
    WriteConfig(config, repo_root / "CHONKY")
    # Simulate the new file in S3 by creating it in cache
    (cache_dir / "new_remote_hash").write_text("Remote modified content")
    Client(repo_root / "CHONKY").sync()
    assert modified_file.exists()
    assert modified_file.read_text() == "Remote modified content"

    # Sync deletes file when remote deleted it
    delete_file = workspace / "to_be_deleted.txt"
    delete_file.write_text("Will be deleted remotely")
    Client(repo_root / "CHONKY").submit()
    config = LoadConfig(repo_root / "CHONKY")
    del config["HEAD"]["to_be_deleted.txt"]
    WriteConfig(config, repo_root / "CHONKY")
    Client(repo_root / "CHONKY").sync()
    assert not delete_file.exists()
    local_config = LoadConfig(workspace / ".HEAD")
    assert "to_be_deleted.txt" not in local_config["HEAD"]


def test_revert_operations(chonky_repo: tuple[Path, Path]) -> None:
    repo_root, workspace = chonky_repo
    client = Client(repo_root / "CHONKY")

    # Revert modified file
    test_file = workspace / "revert_test.txt"
    test_file.write_text("Original")
    client.submit()
    test_file.write_text("Modified")
    client.revert()
    assert test_file.read_text() == "Original"

    # Revert missing file (should restore it)
    missing_file = workspace / "missing_file.txt"
    missing_file.write_text("Should be restored")
    client.submit()
    missing_file.unlink()
    client.revert()
    assert missing_file.exists()
    assert missing_file.read_text() == "Should be restored"

    # Revert added file (should delete it)
    new_file = workspace / "new_file.txt"
    new_file.write_text("Should be deleted on revert")
    client.revert()
    assert not new_file.exists()

    # Revert with no changes (should return early)
    test_file.write_text("Content")
    client.submit()
    client.revert()
    assert test_file.exists()
    assert test_file.read_text() == "Content"


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


def test_submit_operations(chonky_repo: tuple[Path, Path]) -> None:
    repo_root, workspace = chonky_repo

    # Submit with no local changes (early return)
    test_file = workspace / "test.txt"
    test_file.write_text("Content")
    Client(repo_root / "CHONKY").submit()
    Client(repo_root / "CHONKY").submit()
    config = LoadConfig(repo_root / "CHONKY")
    assert "test.txt" in config["HEAD"]

    # Submit with all change types: added, modified, missing
    added_file = workspace / "added.txt"
    added_file.write_text("New file")
    modified_file = workspace / "modified.txt"
    modified_file.write_text("Original")
    Client(repo_root / "CHONKY").submit()
    modified_file.write_text("Modified content")
    missing_file = workspace / "missing.txt"
    missing_file.write_text("Will be deleted")
    Client(repo_root / "CHONKY").submit()
    missing_file.unlink()
    Client(repo_root / "CHONKY").submit()
    config = LoadConfig(repo_root / "CHONKY")
    assert "added.txt" in config["HEAD"]
    assert config["HEAD"]["modified.txt"] == HashFile(modified_file)
    assert "missing.txt" not in config["HEAD"]

    # Submit fails with pending remote changes
    config = LoadConfig(repo_root / "CHONKY")
    config["HEAD"]["remote_new.txt"] = "fake_hash"
    WriteConfig(config, repo_root / "CHONKY")
    test_file.write_text("Modified")
    with pytest.raises(ClientError, match="[Pp]ending remote changes"):
        Client(repo_root / "CHONKY").submit()


def test_resubmit_same_content(chonky_repo: tuple[Path, Path]) -> None:
    repo_root, workspace = chonky_repo

    # Submit file with content A
    file1 = workspace / "file1.txt"
    file1.write_text("Same content")
    Client(repo_root / "CHONKY").submit()

    # Submit different file with same content - should skip upload
    file2 = workspace / "file2.txt"
    file2.write_text("Same content")
    Client(repo_root / "CHONKY").submit()

    config = LoadConfig(repo_root / "CHONKY")
    assert config["HEAD"]["file1.txt"] == config["HEAD"]["file2.txt"]


def test_missing_config_file(temp_dir: Path, mock_s3_env: None) -> None:
    with pytest.raises(ClientError, match="not found"):
        Client(temp_dir / "nonexistent" / "CHONKY")


def test_custom_ignore_patterns(chonky_repo: tuple[Path, Path]) -> None:
    repo_root, workspace = chonky_repo

    # Add ignore pattern to config
    config = LoadConfig(repo_root / "CHONKY")
    config["config"]["ignore"] = "*.tmp ignore_dir/"
    WriteConfig(config, repo_root / "CHONKY")

    # Create files that should be ignored
    (workspace / "test.tmp").write_text("Should be ignored")
    (workspace / "ignore_dir").mkdir()
    (workspace / "ignore_dir" / "file.txt").write_text("Should be ignored")
    (workspace / "normal.txt").write_text("Should be tracked")

    client = Client(repo_root / "CHONKY")
    client.submit()

    final_config = LoadConfig(repo_root / "CHONKY")
    assert "normal.txt" in final_config["HEAD"]
    assert "test.tmp" not in final_config["HEAD"]
    assert "ignore_dir/file.txt" not in final_config["HEAD"]
