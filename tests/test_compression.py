import gzip

from pathlib import Path

import pytest

from chonky.compression import CODECS, CompressionType, ObjectKey


def test_object_key_uncompressed() -> None:
    key = ObjectKey("abc123")
    assert key.content_key == "abc123"
    assert key.type is CompressionType.UNCOMPRESSED
    assert key.filename == "abc123"


def test_object_key_gzip() -> None:
    key = ObjectKey("abc123.gz")
    assert key.content_key == "abc123"
    assert key.type is CompressionType.GZIP
    assert key.filename == "abc123.gz"


def test_object_key_compose() -> None:
    assert ObjectKey.compose("abc", CompressionType.GZIP).filename == "abc.gz"
    assert ObjectKey.compose("abc", CompressionType.UNCOMPRESSED).filename == "abc"


def test_object_key_content_key_never_raises_on_unknown_extension() -> None:
    key = ObjectKey("abc.xyz")
    assert key.content_key == "abc"  # identity is robust to unknown codecs
    with pytest.raises(ValueError):
        _ = key.type  # but resolving the codec fails loud


def test_compression_type_by_value() -> None:
    assert CompressionType("gz") is CompressionType.GZIP
    assert CompressionType("") is CompressionType.UNCOMPRESSED


def test_gzip_codec_round_trip(tmp_path: Path) -> None:
    data = b"hello world " * 1000
    src = tmp_path / "src"
    src.write_bytes(data)
    compressed = tmp_path / "blob.gz"
    CODECS[CompressionType.GZIP].compress(src, compressed)
    assert compressed.stat().st_size < len(data)
    assert gzip.decompress(compressed.read_bytes()) == data

    out = tmp_path / "out"
    CODECS[CompressionType.GZIP].decompress(compressed, out)
    assert out.read_bytes() == data


def test_uncompressed_codec_is_passthrough(tmp_path: Path) -> None:
    data = b"raw bytes"
    src = tmp_path / "src"
    src.write_bytes(data)
    dst = tmp_path / "dst"
    CODECS[CompressionType.UNCOMPRESSED].compress(src, dst)
    assert dst.read_bytes() == data
