#!/usr/bin/env python3
"""Generate vmctl/providers/virtualbox/ostypes.py from a captured ostypes list.

VirtualBox reports a guest OS by its *description* ("Ubuntu (64-bit)") and accepts
only its *id* ("Ubuntu_64"), so vmctl needs the mapping between them for every OS
the product knows -- 227 of them on 7.1.18. That is data, not knowledge, and
hand-maintaining a subset is what broke F-29: any guest outside a 40-entry literal
exported to a config that could not be imported again.

Capture and regenerate:

    ssh winhost "VBoxManage list ostypes" > tests/fixtures/vbox_ostypes.txt
    python scripts/generate-ostypes.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / "tests" / "fixtures" / "vbox_ostypes.txt"
TARGET = ROOT / "vmctl" / "providers" / "virtualbox" / "ostypes.py"

HEADER = '''"""
Every guest OS type VirtualBox knows, and how it names them.

**Generated -- do not edit.** ``scripts/generate-ostypes.py`` builds this from
``tests/fixtures/vbox_ostypes.txt``, captured with ``VBoxManage list ostypes``.

The reason it exists: ``showvminfo`` reports a guest's *description* ("Ubuntu
(64-bit)") while ``createvm --ostype`` accepts only its *id* ("Ubuntu_64"), and
answers "Unknown or invalid guest OS type given" to anything else. The emitter
used to carry a hand-written map of about forty descriptions, so exporting a VM
running anything else produced a config that could not be imported (F-29).

Source: VirtualBox {version}, {count} types.
"""

from typing import Dict, NamedTuple


class OSType(NamedTuple):
    """One entry of ``VBoxManage list ostypes``."""

    id: str
    description: str
    family: str
    architecture: str


#: Every type, by the id ``--ostype`` takes.
OSTYPES: Dict[str, OSType] = {{
{entries}
}}

#: By the description ``showvminfo`` reports, which is what a parsed config holds.
BY_DESCRIPTION: Dict[str, str] = {{t.description: t.id for t in OSTYPES.values()}}

#: Family name -> the most generic 64-bit id in it, for a guest vmctl cannot place
#: exactly. "Other" is the last resort the product itself offers.
GENERIC_BY_FAMILY: Dict[str, str] = {{
{generics}
}}
'''


def parse(text):
    """Yield (id, description, family, architecture) from a captured listing."""
    entry = {}
    for line in text.splitlines():
        line = line.strip()
        match = re.match(r"^ID / Description:\s*(\S+)\s*--\s*(.+)$", line)
        if match:
            if entry:
                yield entry
            entry = {"id": match.group(1), "description": match.group(2).strip()}
            continue
        match = re.match(r"^Family:\s*(\S+)", line)
        if match and entry:
            entry["family"] = match.group(1)
            continue
        match = re.match(r"^Architecture:\s*(.+)$", line)
        if match and entry:
            entry["architecture"] = match.group(1).strip()
    if entry:
        yield entry


def main():
    if not CAPTURE.exists():
        sys.exit(f"missing capture: {CAPTURE}")
    text = CAPTURE.read_text()
    types = [t for t in parse(text) if t.get("family") and t.get("architecture")]
    if not types:
        sys.exit("no OS types found in the capture")

    entries = "\n".join(
        f'    "{t["id"]}": OSType("{t["id"]}", "{t["description"]}", '
        f'"{t["family"]}", "{t["architecture"]}"),'
        for t in types
    )

    # The generic per family, used only when vmctl cannot place a guest exactly.
    # A family gets one only when the product itself offers a version-less member
    # ("Linux_64"); "Windows" has none, and picking its oldest member instead
    # would be a guess dressed as a translation, so it falls back to Other_64.
    known = {t["id"] for t in types}
    generics = {}
    for t in types:
        family = t["family"]
        candidate = f"{family}_64"
        generics[family] = candidate if candidate in known else "Other_64"
    generic_lines = "\n".join(f'    "{f}": "{i}",' for f, i in sorted(generics.items()))

    version = "unknown"
    match = re.search(r"(\d+\.\d+\.\d+)", text)
    if match:
        version = match.group(1)

    TARGET.write_text(
        HEADER.format(version=version, count=len(types), entries=entries, generics=generic_lines)
    )
    # Generated code is held to the same formatting as written code, so that
    # `black --check` in CI does not have to make an exception for it.
    import subprocess

    subprocess.run(
        [sys.executable, "-m", "black", "--quiet", str(TARGET)],
        check=False,
    )
    print(f"wrote {TARGET} ({len(types)} types, {len(generics)} families)")


if __name__ == "__main__":
    main()
