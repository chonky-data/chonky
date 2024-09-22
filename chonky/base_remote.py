import abc

from pathlib import Path

class BaseRemote(abc.ABC):
    def __init__(self, remote_host: str, remote_root: Path, local_root: Path):
        self.remote_host = remote_host
        self.remote_root = remote_root
        self.local_root  = local_root

    @abc.abstractmethod
    def pull(self, keys: list[str]) -> None:
        pass

    @abc.abstractmethod
    def push(self, keys: list[str]) -> None:
        pass

    def has_local(self, key: str) -> bool:
        return self.local_root.joinpath(key).is_file()