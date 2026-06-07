import gzip
import shutil

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class CompressionType(str, Enum):
    # Each member's value is the filename extension a stored blob carries, which is
    # also how it is named in the CHONKY config (e.g. `compression = gz`).
    UNCOMPRESSED = ""
    GZIP = "gz"


class Codec(Protocol):
    def compress(self, src: Path, dst: Path) -> None: ...
    def decompress(self, src: Path, dst: Path) -> None: ...


class Uncompressed:
    def compress(self, src: Path, dst: Path) -> None:
        shutil.copyfile(src, dst)

    def decompress(self, src: Path, dst: Path) -> None:
        shutil.copyfile(src, dst)


class Gzip:
    # Level 6 balances ratio against speed on multi-gigabyte payloads; the stdlib
    # default of 9 is markedly slower for little extra ratio. Streamed so memory
    # stays flat regardless of file size.
    LEVEL = 6

    def compress(self, src: Path, dst: Path) -> None:
        with open(src, "rb") as f_in, gzip.open(dst, "wb", self.LEVEL) as f_out:
            shutil.copyfileobj(f_in, f_out)

    def decompress(self, src: Path, dst: Path) -> None:
        with gzip.open(src, "rb") as f_in, open(dst, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)


CODECS: dict[CompressionType, Codec] = {
    CompressionType.UNCOMPRESSED: Uncompressed(),
    CompressionType.GZIP: Gzip(),
}

# The codec new files are compressed with.
WRITE_CODEC = CompressionType.GZIP


@dataclass(frozen=True)
class ObjectKey:
    # The name of a stored blob: a content identity plus an optional codec
    # extension. The only place a key is split into, or assembled from, its parts.
    filename: str

    @property
    def content_key(self) -> str:
        return self.filename.split(".", 1)[0]

    @property
    def type(self) -> CompressionType:
        _, _, ext = self.filename.partition(".")
        return CompressionType(ext)

    @classmethod
    def compose(cls, content_key: str, type: CompressionType) -> "ObjectKey":
        return cls(f"{content_key}.{type.value}" if type.value else content_key)
