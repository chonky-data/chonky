import abc

from dataclasses import dataclass
from pathlib import Path


@dataclass
class RemoteConfig:
    type: str
    bucket: str
    endpoint: str
    root: str = ""


class BaseRemote(abc.ABC):
    def __init__(self, config: RemoteConfig, local_root: Path):
        self.config = config
        self.local_root = local_root

    @abc.abstractmethod
    def pull(self, keys: list[str]) -> None: ...

    @abc.abstractmethod
    def push(self, keys: list[str]) -> None: ...

    def has_local(self, key: str) -> bool:
        return self.local_root.joinpath(key).is_file()
