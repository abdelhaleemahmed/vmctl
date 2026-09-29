#!/usr/bin/env python3
"""Regenerate tests/golden/* from the current code.

A golden file records the exact commands vmctl emits today. Regenerating is
therefore an explicit statement that a behaviour change is intended: run this,
then *read the diff* before committing it.

    python tests/regenerate_golden.py
    git diff tests/golden/
"""

import sys
from pathlib import Path

# The package first, then this directory *in front of it*: the repository root
# also has a conftest.py (the import shim for an uninstalled clone), and it would
# otherwise shadow the one next to this script, which is where the helpers live.
sys.path.insert(0, str(Path(__file__).parent.parent))  # the package
sys.path.insert(0, str(Path(__file__).parent))  # conftest helpers

from conftest import GOLDEN, VM_LABELS, emit, parse_label  # noqa: E402


def main() -> int:
    GOLDEN.mkdir(exist_ok=True)
    written = []

    for label in VM_LABELS:
        vm = parse_label(label)
        (GOLDEN / f"emit_{label}.txt").write_text(emit(vm))
        written.append(f"emit_{label}.txt")

    # Hand-built configs, imported lazily so this script has no pytest dependency.
    from test_emitter import build_minimal, build_full

    for name, vm in (("minimal", build_minimal()), ("full", build_full())):
        (GOLDEN / f"emit_{name}.txt").write_text(emit(vm))
        written.append(f"emit_{name}.txt")

    print(f"Wrote {len(written)} golden files to {GOLDEN}:")
    for name in written:
        print(f"  {name}")
    print("\nNow review: git diff tests/golden/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
