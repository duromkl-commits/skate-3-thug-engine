"""Read Tony Hawk's Underground `.pre` archives (LZSS-compressed file bundles).

Layout from the THUG source (Sys/File/PRE.cpp), version 0xABCD0003:
  u32 total_size, u32 version, u32 num_files
  per file: i32 data_size, i32 compressed_size (0 = stored), i16 name_len, u16 pad,
            u32 name_checksum, char name[name_len], data, padded to 4 bytes.
"""
import pathlib
import struct
import sys

PRE_VERSION = 0xABCD0003
RING = 4096
MATCH_LIMIT = 18
THRESHOLD = 2


def decode_lzss(data: bytes, out_size: int) -> bytes:
    text = bytearray(b" " * (RING + MATCH_LIMIT - 1))
    r = RING - MATCH_LIMIT
    out = bytearray()
    pos, flags, n = 0, 0, len(data)
    while pos < n:
        flags >>= 1
        if not flags & 0x100:
            flags = data[pos] | 0xFF00
            pos += 1
            if pos >= n:
                break
        if flags & 1:
            c = data[pos]
            pos += 1
            out.append(c)
            text[r] = c
            r = (r + 1) & (RING - 1)
        else:
            if pos + 1 >= n:
                break
            i, j = data[pos], data[pos + 1]
            pos += 2
            i |= (j & 0xF0) << 4
            for k in range((j & 0x0F) + THRESHOLD + 1):
                c = text[(i + k) & (RING - 1)]
                out.append(c)
                text[r] = c
                r = (r + 1) & (RING - 1)
    if len(out) < out_size:
        raise ValueError(f"LZSS stream ended early: {len(out)} of {out_size} bytes")
    return bytes(out[:out_size])


def read_pre(path) -> dict[str, bytes]:
    """Return {archive path (lower case, forward slashes): file bytes}."""
    raw = pathlib.Path(path).read_bytes()
    _total, version, count = struct.unpack_from("<III", raw, 0)
    if version != PRE_VERSION:
        raise ValueError(f"{path}: PRE version {version:#x}, expected {PRE_VERSION:#x}")
    files, off = {}, 12
    for _ in range(count):
        size, packed, name_len = struct.unpack_from("<iih", raw, off)
        name = raw[off + 16:off + 16 + name_len].split(b"\0", 1)[0].decode("latin-1")
        start = off + 16 + name_len
        stored = packed or size
        blob = raw[start:start + stored]
        files[name.replace("\\", "/").lower()] = decode_lzss(blob, size) if packed else blob
        off = start + ((stored + 3) & ~3)
    return files


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: thug_pre.py <file.pre> [out_dir]")
    entries = read_pre(sys.argv[1])
    out_dir = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None
    for name, data in entries.items():
        print(f"{len(data):>10}  {name}")
        if out_dir:
            target = out_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
