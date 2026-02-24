#!/usr/bin/env python3
"""Decompile rapid_vfs_cache.bin: list paths and summarize by extension."""
import os
import struct
import sys
import zlib
from collections import Counter
from datetime import datetime, timezone


def _parse_metadata(raw, path_block_end):
    remaining = len(raw) - path_block_end
    if remaining < 4:
        return None
    meta_len = struct.unpack_from("<I", raw, len(raw) - 4)[0]
    if meta_len <= 0 or meta_len > remaining - 4 or meta_len > (1 << 20):
        return None
    start = len(raw) - 4 - meta_len
    if start < path_block_end:
        return None
    meta = raw[start : start + meta_len]
    off = 0
    if off + 8 > len(meta):
        return None
    (build_time_ms,) = struct.unpack_from("<Q", meta, off)
    off += 8
    ext_counter = Counter()
    if off + 4 > len(meta):
        return None
    (num_ext,) = struct.unpack_from("<I", meta, off)
    off += 4
    for _ in range(num_ext):
        if off + 2 > len(meta):
            return None
        (slen,) = struct.unpack_from("<H", meta, off)
        off += 2
        if off + slen + 4 > len(meta):
            return None
        ext_counter[meta[off : off + slen].decode("utf-8")] = struct.unpack_from("<I", meta, off + slen)[0]
        off += slen + 4
    if off + 4 > len(meta):
        return None
    (num_root,) = struct.unpack_from("<I", meta, off)
    off += 4
    root_counter = Counter()
    for _ in range(num_root):
        if off + 2 > len(meta):
            return None
        (slen,) = struct.unpack_from("<H", meta, off)
        off += 2
        if off + slen + 4 > len(meta):
            return None
        root_counter[meta[off : off + slen].decode("utf-8")] = struct.unpack_from("<I", meta, off + slen)[0]
        off += slen + 4
    return (build_time_ms, ext_counter, root_counter)


def _format_build_time(build_time_ms):
    if build_time_ms is None:
        return "unknown"
    dt = datetime.fromtimestamp(build_time_ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def main():
    cache_path = os.path.join(os.path.dirname(__file__), "..", "cache", "rapid_vfs_cache.bin")
    if len(sys.argv) > 1:
        cache_path = sys.argv[1]

    with open(cache_path, "rb") as f:
        compressed = f.read()

    raw = zlib.decompress(compressed)
    offset = 0
    (num_files,) = struct.unpack_from("<I", raw, offset)
    offset += 4

    paths = []
    for _ in range(num_files):
        (path_len,) = struct.unpack_from("<H", raw, offset)
        offset += 2
        path = raw[offset : offset + path_len].decode("utf-8")
        offset += path_len
        paths.append(path)

    path_block_end = offset
    parsed = _parse_metadata(raw, path_block_end)
    if parsed is not None:
        build_time_ms, ext_counter, root_counter = parsed
    else:
        build_time_ms = None
        ext_counter = Counter()
        for p in paths:
            _, ext = os.path.splitext(p)
            ext_counter[ext.lower() if ext else "(no ext)"] += 1
        root_counter = Counter()
        for p in paths:
            parts = p.split("\\")
            root_counter[parts[0] if parts else ""] += 1

    print("=== RAPID cache decompile ===\n")
    print(f"Total paths: {len(paths)}")
    print(f"Built: {_format_build_time(build_time_ms)}\n")
    print("--- Extensions (count) ---")
    for ext, count in ext_counter.most_common():
        print(f"  {ext!r}: {count}")
    print("\n--- Mod roots (top 40) ---")
    for root, count in root_counter.most_common(40):
        print(f"  {root!r}: {count}")
    print("\n--- Sample paths per extension (first 2) ---")
    seen_ext = set()
    for p in paths:
        _, ext = os.path.splitext(p)
        ext = ext.lower() if ext else "(no ext)"
        if ext not in seen_ext:
            seen_ext.add(ext)
            samples = [q for q in paths if os.path.splitext(q)[1].lower() == (ext if ext != "(no ext)" else "")]
            for s in samples[:2]:
                print(f"  {ext!r}: {s!r}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
