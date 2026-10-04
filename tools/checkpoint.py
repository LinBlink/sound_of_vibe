"""Export incremental git-am patches when the repository metadata is read-only.

This writes working-tree artifacts only; it never updates Git metadata.
"""

import argparse
import difflib
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subject")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = root / "checkpoints"
    output.mkdir(exist_ok=True)
    state = output / "snapshot.json"
    previous = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
    paths = [root / ".gitignore", root / "pyproject.toml", root / "README.md", root / "TODO.md"]
    for directory in ("src", "tests", "tools", "examples"):
        paths.extend(p for p in (root / directory).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and p.suffix != ".pyc"
                     and not any(part.endswith(".egg-info") for part in p.parts))
    current = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
               for p in sorted(paths) if p.exists()}
    diffs = []
    for name in sorted(previous.keys() | current.keys()):
        before, after = previous.get(name, ""), current.get(name, "")
        if before == after:
            continue
        diffs.append(f"diff --git a/{name} b/{name}\n")
        if name not in previous:
            diffs.append("new file mode 100644\n")
        elif name not in current:
            diffs.append("deleted file mode 100644\n")
        for line in difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{name}" if name in previous else "/dev/null",
            tofile=f"b/{name}" if name in current else "/dev/null"):
            diffs.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    if not diffs:
        raise SystemExit("No changes since the previous checkpoint.")
    number = len(list(output.glob("*.patch"))) + 1
    digest = hashlib.sha1("".join(diffs).encode()).hexdigest()
    author = subprocess.check_output(["git", "config", "user.name"], cwd=root, text=True).strip()
    email = subprocess.check_output(["git", "config", "user.email"], cwd=root, text=True).strip()
    patch = (f"From {digest} Mon Sep 17 00:00:00 2001\n"
             f"From: {author} <{email}>\n"
             f"Date: {format_datetime(datetime.now(timezone.utc))}\n"
             f"Subject: [PATCH] {args.subject}\n\n---\n" + "".join(diffs)
             + "-- \n2.0.0\n")
    destination = output / f"{number:02d}.patch"
    destination.write_text(patch, encoding="utf-8", newline="\n")
    state.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    (output / f"{number:02d}.snapshot.json").write_text(
        json.dumps(current, ensure_ascii=False), encoding="utf-8")
    print(destination.relative_to(root))


if __name__ == "__main__":
    main()
