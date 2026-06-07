import os
import tempfile
import time

from collections import OrderedDict
from configparser import ConfigParser as BaseConfigParser
from functools import partial
from hashlib import sha1
from multiprocessing.dummy import Pool as ThreadPool
from pathlib import Path
from platformdirs import user_cache_dir
from tqdm import tqdm
from typing import Generator, Optional

from chonky.base_remote import RemoteConfig
from chonky.compression import CODECS, WRITE_CODEC, CompressionType, ObjectKey
from chonky.make_remote import make_remote

# Codec passes (compress on submit, decompress on sync/revert) are CPU-bound;
# gzip releases the GIL, so a thread pool sized to the CPU count parallelizes them.
MAX_CONCURRENT_CODEC = os.cpu_count() or 4

# Keep a compressed blob only when it saves at least this fraction of the original;
# a marginal gain isn't worth paying the decompress cost on every read.
COMPRESSION_MIN_SAVINGS = 0.10


class ClientError(Exception):
    pass


def get_cache_path() -> Path:
    return Path(
        user_cache_dir(appname="chonky", appauthor="chonky", ensure_exists=True)
    )


# Check if a file path matches any ignore pattern
def MatchesIgnorePattern(file_path: Path, patterns: list[str]) -> bool:
    return any(file_path.match(pattern) for pattern in patterns)


# Provides an iterator for files under a directory located recursively.
# Skips files and directories that match ignore patterns.
def RecursiveFiles(
    workspace_root: Path, ignore_patterns: list[str]
) -> Generator[Path, None, None]:
    for curr_root, dirs, files in os.walk(workspace_root):
        rel_root = Path(curr_root).relative_to(workspace_root)
        # Filter directories based on ignore patterns
        dirs[:] = [
            d for d in dirs if not MatchesIgnorePattern(rel_root / d, ignore_patterns)
        ]
        # Filter files based on ignore patterns
        for file in files:
            file_path = rel_root / file
            if not MatchesIgnorePattern(file_path, ignore_patterns):
                yield file_path


def HashFile(path: Path, buffer_size: int = 65536) -> str:
    hasher = sha1()
    with path.open("rb") as f:
        while data := f.read(buffer_size):
            hasher.update(data)
    return hasher.hexdigest()


class ConfigParser(BaseConfigParser):
    def optionxform(self, optionstr: str) -> str:  # case sensitive
        return optionstr


def LoadConfig(path: Path) -> ConfigParser:
    config = ConfigParser()
    config.read(path)
    return config


def BuildConfigForRoot(
    workspace_root: Path, ignore_patterns: list[str]
) -> ConfigParser:
    config = ConfigParser()
    config["HEAD"] = OrderedDict(
        [
            (Path(file).as_posix(), HashFile(workspace_root / file))
            for file in RecursiveFiles(workspace_root, ignore_patterns)
        ]
    )
    return config


def WriteConfig(config: ConfigParser, path: Path) -> None:
    # Sort file list to ensure configs are mergable / diffable...
    config["HEAD"] = OrderedDict(sorted(config["HEAD"].items(), key=lambda t: t[0]))
    with path.open("w") as f:
        config.write(f)


class ConfigDiff:
    def __init__(self, config_a: ConfigParser, config_b: ConfigParser):
        HEAD_a = config_a["HEAD"]
        HEAD_b = config_b["HEAD"]
        self.added = HEAD_b.keys() - HEAD_a
        self.missing = HEAD_a.keys() - HEAD_b
        # Compare content identity, not the full key: a workspace-derived bare hash
        # must not read as "modified" against a stored, compression-suffixed key.
        self.modified = {
            k
            for k in HEAD_a.keys() & HEAD_b
            if ObjectKey(HEAD_a[k]).content_key != ObjectKey(HEAD_b[k]).content_key
        }

    def __bool__(self) -> bool:
        return bool(self.added or self.missing or self.modified)

    def changed_files(self) -> set[str]:
        return self.added | self.missing | self.modified

    def print(self) -> None:
        for f in self.added:
            print(f"  added     {f}")
        for f in self.missing:
            print(f"  missing   {f}")
        for f in self.modified:
            print(f"  modified  {f}")


# Check for and list conflicts between two diffs...
# Typically used for seeing if incoming remote changes conflict with workspace changes
def ComputeConflicts(remote_diff: ConfigDiff, working_diff: ConfigDiff) -> list[str]:
    return sorted(remote_diff.changed_files() & working_diff.changed_files())


class Client:
    def __init__(self, config_path: Path):
        if not config_path.is_file():
            raise ClientError(f"Config={config_path} was not found")
        # ensure local cache directory exists...
        self.local_cache_path = get_cache_path()
        # load the chonky config and HEAD state...
        self.config_path = config_path
        self.config = LoadConfig(self.config_path)
        # create or load local repository HEAD state...
        if self.local_config_path.is_file():
            self.local_config = LoadConfig(self.local_config_path)
        else:
            self.workspace_path.mkdir(parents=True, exist_ok=True)
            self.local_config = ConfigParser()
            self.local_config.add_section("HEAD")
            WriteConfig(self.local_config, self.local_config_path)

    @property
    def remote_config(self) -> RemoteConfig:
        config = self.config["config"]
        return RemoteConfig(
            type=config["type"],
            bucket=config["bucket"],
            endpoint=config["endpoint"],
            root=config.get("root", ""),
        )

    @property
    def workspace_path(self) -> Path:
        return self.config_path.parent.joinpath(
            self.config["config"]["workspace"]
        ).resolve()

    @property
    def local_config_path(self) -> Path:
        return self.workspace_path.joinpath(".HEAD")

    @property
    def ignore_patterns(self) -> list[str]:
        builtin_ignores = [".HEAD"]
        if "ignore" not in self.config["config"]:
            return builtin_ignores
        ignore_text = self.config["config"]["ignore"]
        return ignore_text.split() + builtin_ignores

    # Syncs up the local cache to the remote
    def cache_pull(self) -> None:
        remote = make_remote(self.remote_config, self.local_cache_path)
        remote.pull(
            [key for key in self.config["HEAD"].values() if not remote.has_local(key)]
        )

    # Syncs up the remote cache to the local
    def cache_push(self, touched_files: set[str]) -> None:
        remote = make_remote(self.remote_config, self.local_cache_path)
        remote.push(list({self.local_config["HEAD"][file] for file in touched_files}))

    # The key this content is already cached under, if any — content new to this repo
    # may sit in the shared cache from a sibling repo against the same bucket, to be
    # reused rather than recompressed. We don't guarantee deterministic compression, so
    # two encodings could rarely coexist; check the default codec first to prefer it.
    def _cached_key(self, content_key: str) -> Optional[ObjectKey]:
        default_first = [
            WRITE_CODEC,
            *(c for c in CompressionType if c is not WRITE_CODEC),
        ]
        for codec_type in default_first:
            key = ObjectKey.compose(content_key, codec_type)
            if self.local_cache_path.joinpath(key.filename).is_file():
                return key
        return None

    # Pure per-blob work, safe to run in parallel: compress one workspace file
    # (keeping the result only if it shrinks), then atomically commit it to the
    # cache. Returns (content_key, stored key) so unordered results can be matched.
    def _stage(self, item: tuple[str, str], start_time: float) -> tuple[str, ObjectKey]:
        content_key, file = item
        file_path = self.workspace_path.joinpath(file)
        # Stage inside a temp dir on the cache's mount: the commit stays an atomic,
        # zero-copy rename, and the temp is cleaned up automatically (even on error),
        # so nothing leaks and concurrent submits can't collide.
        with tempfile.TemporaryDirectory(dir=self.local_cache_path) as tmp:
            temp_path = Path(tmp).joinpath("blob")
            # Compress with the write codec; fall back to raw if it didn't save enough.
            codec_type = WRITE_CODEC
            CODECS[codec_type].compress(file_path, temp_path)
            if temp_path.stat().st_size > file_path.stat().st_size * (
                1 - COMPRESSION_MIN_SAVINGS
            ):
                codec_type = CompressionType.UNCOMPRESSED
                CODECS[codec_type].compress(file_path, temp_path)
            # Detect files modified after workspace hashing began.
            if os.stat(file_path).st_mtime > start_time:
                raise ClientError(f"{file} was modified while Chonky was running!")
            final = ObjectKey.compose(content_key, codec_type)
            os.rename(src=temp_path, dst=self.local_cache_path.joinpath(final.filename))
            return content_key, final

    # Decompress (or copy, for raw keys) a cached blob into the workspace.
    def _materialize(self, item: tuple[str, Path]) -> None:
        key, dst = item
        dst.parent.mkdir(parents=True, exist_ok=True)
        CODECS[ObjectKey(key).type].decompress(self.local_cache_path.joinpath(key), dst)

    def _materialize_all(self, items: list[tuple[str, Path]]) -> None:
        with ThreadPool(MAX_CONCURRENT_CODEC) as pool:
            for _ in tqdm(
                pool.imap_unordered(self._materialize, items),
                total=len(items),
                desc="Extracting",
                unit="file",
            ):
                pass

    def status(self) -> None:
        working_config = BuildConfigForRoot(self.workspace_path, self.ignore_patterns)
        remote_diff = ConfigDiff(self.local_config, self.config)
        working_diff = ConfigDiff(self.local_config, working_config)
        # Check for conflicts...
        if conflicts := ComputeConflicts(remote_diff, working_diff):
            print(
                f"Conflicts must be resolved before you can sync or submit: {conflicts}"
            )
        # Check to see if there are any remote changes we can pull in...
        if remote_diff:
            print("Remote changes are available, run “chonky sync” to update:")
            remote_diff.print()
        else:
            print("Workspace is up to date with the remote.")
        # Check to see if the local workspace has any changes that can be submitted...
        if working_diff:
            print("Workspace has changes:")
            working_diff.print()
        else:
            print("Workspace has no changes to submit.")

    def sync(self) -> None:
        working_config = BuildConfigForRoot(self.workspace_path, self.ignore_patterns)
        remote_diff = ConfigDiff(self.local_config, self.config)
        working_diff = ConfigDiff(self.local_config, working_config)
        if not remote_diff:
            # No incoming changes, we can early out...
            return
        if conflicts := ComputeConflicts(remote_diff, working_diff):
            raise ClientError(f"Conflicts must be resolved first: {conflicts}")
        # Pull from remote to local cache...
        self.cache_pull()
        # Commit changes to local cache and workspace...
        changed = remote_diff.added | remote_diff.modified
        for file in changed:
            self.local_config["HEAD"][file] = self.config["HEAD"][file]
        self._materialize_all(
            [
                (self.config["HEAD"][file], self.workspace_path.joinpath(file))
                for file in changed
            ]
        )
        for file in remote_diff.missing:
            del self.local_config["HEAD"][file]
            self.workspace_path.joinpath(file).unlink()
        # Commit local HEAD...
        WriteConfig(self.local_config, self.local_config_path)

    def submit(self) -> None:
        start_time = (
            time.time()
        )  # used for detecting files that changed after hashing...
        working_config = BuildConfigForRoot(self.workspace_path, self.ignore_patterns)
        working_diff = ConfigDiff(self.local_config, working_config)
        if not working_diff:
            # No localing changes, we can early exit...
            return
        if ConfigDiff(self.local_config, self.config):
            raise ClientError(
                f"Pending remote changes are available that must first be resolved. Run 'chonky sync' first."
            )
        # Resolve each working file to the key its content is stored under: reuse a
        # blob already in the cache (possibly staged by a sibling repo against the same
        # bucket), otherwise None for a genuine miss, staged below. Resolution is by
        # cache presence alone, never the recorded HEAD -- a cleared cache must re-stage
        # content we still track. Misses are deduped by content so each uncached blob is
        # compressed exactly once, on disjoint paths, and staged in parallel.
        keys = dict(working_config["HEAD"])  # file -> bare content hash
        existing = {
            file: self._cached_key(content_key) for file, content_key in keys.items()
        }
        to_stage = {keys[file]: file for file, key in existing.items() if key is None}
        staged: dict[str, ObjectKey] = {}
        if to_stage:
            with ThreadPool(MAX_CONCURRENT_CODEC) as pool:
                stage = partial(self._stage, start_time=start_time)
                staged = dict(
                    tqdm(
                        pool.imap_unordered(stage, to_stage.items()),
                        total=len(to_stage),
                        desc="Compressing",
                        unit="file",
                    )
                )
        working_config["HEAD"] = {
            file: (key or staged[keys[file]]).filename for file, key in existing.items()
        }
        # Validate the working HEAD has not changed since
        # Overwrite local and remote HEADs (in memory)...
        self.local_config["HEAD"] = working_config["HEAD"]
        self.config["HEAD"] = working_config["HEAD"]
        # Push added/modified objects to the remote...
        self.cache_push(working_diff.added | working_diff.modified)
        # Commit the new local and remote HEAD...
        WriteConfig(self.local_config, self.local_config_path)
        WriteConfig(self.config, self.config_path)

    def revert(self) -> None:
        working_config = BuildConfigForRoot(self.workspace_path, self.ignore_patterns)
        working_diff = ConfigDiff(self.local_config, working_config)
        if not working_diff:
            # No localing changes, we can early exit...
            return
        print("Reverting:")
        for f in working_diff.changed_files():
            print(f"  {f}")
        materializations = [
            (self.local_config["HEAD"][file], self.workspace_path.joinpath(file))
            for file in working_diff.modified | working_diff.missing
        ]
        self._materialize_all(materializations)
        for file in working_diff.added:
            self.workspace_path.joinpath(file).unlink()
