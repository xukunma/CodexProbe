"""Losslessly store HTTP entity bodies and expose compressed JSON and SSE as text."""
import base64
from compression import zstd
import gzip
import zlib


def capture(data: bytes, encoding: str) -> dict:
    """Preserve original entity bytes, including incomplete or non-UTF-8 payloads."""
    if not encoding or encoding.lower() == "identity":
        try:
            return {"text": data.decode("utf-8")}
        except UnicodeDecodeError:
            pass
    result = {"base64": base64.b64encode(data).decode("ascii"), "encoding": encoding}
    decoders = {"gzip": gzip.decompress, "deflate": zlib.decompress, "zstd": zstd.decompress}
    try:
        decoded = data
        for codec in reversed(encoding.lower().split(",")):
            decoded = decoders[codec.strip()](decoded)
        result["text"] = decoded.decode("utf-8")
    except (KeyError, OSError, EOFError, ValueError, zlib.error, zstd.ZstdError):
        pass
    return result
