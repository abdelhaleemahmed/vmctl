#!/usr/bin/env python3
"""Write the generated documentation tables (E-18).

Three files, none of them hand-written:

* ``docs/features.md``  -- every field a config file may contain
* ``docs/providers.md`` -- what each hypervisor supports
* ``docs/commands.md``  -- the command list, as ``--help`` reports it

They are generated because they drifted when they were not: ``features.md`` still
described ``ostype`` as a VirtualBox identifier, with no ``storage``, no ``guest_os``
and no ``port_forwards``, long after the model had them. The rendering lives in
:mod:`vmctl.core.docgen` so the tests can check the committed copies are current --
the same arrangement as ``vmctl.schema.json``.

Regenerate after any change to the model or to a provider's capability declaration:

    python scripts/generate-docs.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vmctl.providers  # noqa: E402,F401  (registers the built-in providers)
from vmctl.core import docgen  # noqa: E402

#: What is written where. The content is a function so nothing is rendered twice.
OUTPUTS = {
    ROOT / "docs" / "features.md": docgen.field_reference,
    ROOT / "docs" / "providers.md": docgen.provider_matrix,
    ROOT / "docs" / "commands.md": docgen.command_reference,
}


def main() -> int:
    """Write every generated file, reporting which changed."""
    changed = 0
    for path, render in OUTPUTS.items():
        text = render()
        if not text.endswith("\n"):
            text += "\n"
        before = path.read_text() if path.exists() else ""
        path.write_text(text)
        state = "unchanged" if before == text else "written"
        changed += before != text
        print(f"{state}: {path.relative_to(ROOT)} ({len(text.splitlines())} lines)")
    return 0 if changed == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
