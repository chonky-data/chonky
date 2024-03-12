import os
import shutil
import time

from collections import OrderedDict
from configparser import ConfigParser
from hashlib import sha1
from pathlib import Path
from platformdirs import user_cache_dir # type: ignore
from urllib.parse import urlparse

from chonky.make_remote import make_remote

class ClientError(Exception):
    pass

# Provides an iterator for files under a directory located recursively.
# Skips "hidden" files and directories that begin with ".".
def RecursiveFiles(workspace_root: Path):
    for curr_root, dirs, files in os.walk(workspace_root):
        rel_root = Path(curr_root).relative_to(workspace_root)
        dirs[:]  = [x for x in dirs  if not x.startswith(".")]
        files[:] = [x for x in files if not x.startswith(".")]
        for file in files:
            yield rel_root / file

def HashFile(path: Path, buffer_size=65536):
    hasher = sha1()
    with path.open("rb") as f:
        while data := f.read(buffer_size):
            hasher.update(data)
    return hasher.hexdigest()

def MakeConfig():
    config = ConfigParser(dict_type=OrderedDict)
    config.optionxform = str # case sensitive
    return config

def LoadConfig(path: Path):
    config = MakeConfig()
    config.read(path)
    return config

def BuildConfigForRoot(workspace_root: Path):
    config = MakeConfig()
    config["HEAD"] = OrderedDict([
        (Path(file).as_posix() ,HashFile(workspace_root / file))
        for file in RecursiveFiles(workspace_root)
    ])
    return config

def WriteConfig(config: ConfigParser, path: Path):
    # Sort file list to ensure configs are mergable / diffable...
    config["HEAD"] = OrderedDict(sorted(config["HEAD"].items(), key=lambda t: t[0]))
    with path.open("w") as f:
        config.write(f)

class ConfigDiff:
    def __init__(self, config_a: ConfigParser, config_b: ConfigParser):
        HEAD_a = config_a["HEAD"]
        HEAD_b = config_b["HEAD"]
        self.added    = HEAD_b.keys() - HEAD_a
        self.missing  = HEAD_a.keys() - HEAD_b
        self.modified = {k for k in HEAD_a.keys() & HEAD_b if HEAD_a[k] != HEAD_b[k]}

    def __bool__(self):
        return bool(self.added or self.missing or self.modified)

    def changed_files(self):
        return self.added | self.missing | self.modified

    def print(self):
        for f in self.added:
            print(f"  added     {f}")
        for f in self.missing:
            print(f"  missing   {f}")
        for f in self.modified:
            print(f"  modified  {f}")

# Check for and list conflicts between two diffs...
# Typically used for seeing if incoming remote changes conflict with workspace changes
def ComputeConflicts(remote_diff: ConfigDiff, working_diff: ConfigDiff):
    return sorted(remote_diff.changed_files() & working_diff.changed_files())

class Client:
    def __init__(self, config_path: Path):
        if not config_path.is_file():
            raise ClientError(f"Config={config_path} was not found")
        # ensure local cache directory exists...
        self.local_cache_path = Path(user_cache_dir(appname="chonky", appauthor="chonky", ensure_exists=True))
        # load the remote repository config and HEAD state...
        self.remote_config_path = config_path
        self.remote_config = LoadConfig(self.remote_config_path)
        # create or load local repository HEAD state...
        if self.local_config_path.is_file():
            self.local_config = LoadConfig(self.local_config_path)
        else:
            self.workspace_path.mkdir(parents=True, exist_ok=True)
            self.local_config = MakeConfig()
            self.local_config.add_section("HEAD")
            WriteConfig(self.local_config, self.local_config_path)

    @property
    def remote_uri(self):
        return self.remote_config["config"]["remote"]

    @property
    def workspace_path(self):
        return self.remote_config_path.parent.joinpath(self.remote_config["config"]["workspace"]).resolve()

    @property
    def local_config_path(self):
        return self.workspace_path.joinpath(".HEAD")

    # Syncs up the local cache to the remote
    def cache_pull(self):
        remote = make_remote(remote_uri=self.remote_uri, local_root=self.local_cache_path)
        remote.pull([
            key
            for key in self.remote_config["HEAD"].values()
            if not remote.has_local(key)
        ])

    # Syncs up the remote cache to the local
    def cache_push(self, touched_files: set):
        remote = make_remote(remote_uri=self.remote_uri, local_root=self.local_cache_path)
        remote.push(list({
            self.local_config["HEAD"][file]
            for file in touched_files
        }))

    def status(self):
        working_config = BuildConfigForRoot(self.workspace_path)
        remote_diff    = ConfigDiff(self.local_config, self.remote_config)
        working_diff   = ConfigDiff(self.local_config, working_config)
        # Check for conflicts...
        if conflicts := ComputeConflicts(remote_diff, working_diff):
            print(f"Conflicts must be resolved before you can sync or submit: {conflicts}")
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

    def sync(self):
        working_config = BuildConfigForRoot(self.workspace_path)
        remote_diff    = ConfigDiff(self.local_config, self.remote_config)
        working_diff   = ConfigDiff(self.local_config, working_config)
        if not remote_diff:
            # No incoming changes, we can early out...
            return
        if conflicts := ComputeConflicts(remote_diff, working_diff):
            raise ClientError(f"Conflicts must be resolved first: {conflicts}")
        # Pull from remote to local cache...
        self.cache_pull()
        # Commit changes to local cache and workspace...
        for file in remote_diff.added | remote_diff.modified:
            key = self.remote_config["HEAD"][file]
            self.local_config["HEAD"][file] = key
            file_path = self.workspace_path.joinpath(file)
            cache_path = self.local_cache_path.joinpath(key)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src=cache_path, dst=file_path)
        for file in remote_diff.missing:
            del self.local_config["HEAD"][file]
            self.workspace_path.joinpath(file).unlink()
        # Commit local HEAD...
        WriteConfig(self.local_config,  self.local_config_path)

    def submit(self):
        start_time     = time.time() # used for detecting files that changed after hashing...
        working_config = BuildConfigForRoot(self.workspace_path)
        working_diff   = ConfigDiff(self.local_config, working_config)
        if not working_diff:
            # No localing changes, we can early exit...
            return
        if ConfigDiff(self.local_config, self.remote_config):
            raise ClientError(f"Pending remote changes are available that must first be resolved. Run 'chonky sync' first.")
        # Update the local HEAD to match the working HEAD...
        for file,key in working_config["HEAD"].items():
            cache_path = self.local_cache_path.joinpath(key)
            if not cache_path.is_file():
                file_path = self.workspace_path.joinpath(file)
                # Attempt to ensure atomic submission...
                # 1) copy to temp file
                temp_path = cache_path.parent.joinpath(f"temp.{cache_path.name}")
                shutil.copy2(src=file_path, dst=temp_path)
                # 2) verify file was not modified before workspace hashing was performed
                if os.stat(temp_path).st_mtime > start_time:
                    temp_path.unlink()
                    raise ClientError(f"{file} was modified while Chonky was running!")
                # 3) commit final name
                os.rename(src=temp_path, dst=cache_path)
        # Validate the working HEAD has not changed since
        # Overwrite local and remote HEADs (in memory)...
        self.local_config["HEAD"]  = working_config["HEAD"]
        self.remote_config["HEAD"] = working_config["HEAD"]
        # Push added/modified objects to the remote...
        self.cache_push(working_diff.added | working_diff.modified)
        # Commit the new local and remote HEAD...
        WriteConfig(self.local_config,  self.local_config_path)
        WriteConfig(self.remote_config, self.remote_config_path)

    def revert(self):
        working_config = BuildConfigForRoot(self.workspace_path)
        working_diff = ConfigDiff(self.local_config, working_config)
        if not working_diff:
            # No localing changes, we can early exit...
            return
        print("Reverting:")
        for f in working_diff.changed_files():
            print(f"  {f}")
        for file in working_diff.modified | working_diff.missing:
            key = self.local_config["HEAD"][file]
            file_path = self.workspace_path.joinpath(file)
            cache_path = self.local_cache_path.joinpath(key)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src=cache_path, dst=file_path)
        for file in working_diff.added:
            self.workspace_path.joinpath(file).unlink()