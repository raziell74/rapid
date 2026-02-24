#!/usr/bin/env python3
"""Decompile rapid_vfs_cache.bin: list paths and summarize by extension."""
import os
import struct
import sys
import zlib
from collections import Counter

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

    # Extensions (lowercase, include dot)
    ext_counter = Counter()
    for p in paths:
        _, ext = os.path.splitext(p)
        ext_counter[ext.lower() if ext else "(no ext)"] += 1

    # Mod "roots" (first path component) for context
    root_counter = Counter()
    for p in paths:
        parts = p.split("\\")
        root_counter[parts[0] if parts else ""] += 1

    print("=== RAPID cache decompile ===\n")
    print(f"Total paths: {len(paths)}\n")
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
