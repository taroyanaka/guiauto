#!/usr/bin/env python3
"""Search recursively for color code e3147b in files and print matches.

Usage:
  python ollama_test5.py [path] [-e .py] [-j]

Examples:
  python ollama_test5.py .
  python ollama_test5.py . -e .py -e .txt
  python ollama_test5.py ./only_local_3_series -j
"""
import argparse
import json
import os
import re
import sys


def iter_files(root, include_exts=None):
    for dirpath, dirs, files in os.walk(root):
        for fname in files:
            if include_exts:
                if not any(fname.lower().endswith(ext) for ext in include_exts):
                    continue
            yield os.path.join(dirpath, fname)


def search_file(path, pattern):
    results = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                for m in pattern.finditer(line):
                    col = m.start() + 1
                    results.append((i, col, m.group(), line.rstrip("\n")))
    except Exception:
        return []
    return results


def main():
    parser = argparse.ArgumentParser(description="Find color code e3147b in files.")
    parser.add_argument("path", nargs="?", help="Root path to search (positional)")
    parser.add_argument("-i", "--input-dir", dest="input_dir", help="Alias for root path (like ollama_test3.py)")
    parser.add_argument("-e", "--ext", action="append", help="File extension filter, e.g. .py .txt", default=[])
    parser.add_argument("-j", "--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    pattern = re.compile(r"#?e3147b", re.IGNORECASE)

    matches = []
    root = args.input_dir or args.path or "."
    for path in iter_files(root, include_exts=[ext.lower() for ext in args.ext] if args.ext else None):
        res = search_file(path, pattern)
        if res:
            if args.json:
                matches.append({"file": path, "matches": [{"line": ln, "col": col, "text": txt, "context": ctx} for ln, col, txt, ctx in res]})
            else:
                for ln, col, txt, ctx in res:
                    print(f"{path}:{ln}:{col}: {txt} >> {ctx}")

    if args.json:
        print(json.dumps(matches, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
