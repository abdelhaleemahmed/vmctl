"""
A provider-neutral description of work to be done.

Providers used to return ``List[List[str]]`` -- a list of argv lists. That type
*is* the VirtualBox command line, and it cannot express what other hypervisors
need: libvirt takes an XML document handed to ``virsh define``, VMware needs a
``.vmx`` file written and then ``vmrun`` called, Hyper-V wants a PowerShell
script, Proxmox wants HTTP requests. A :class:`Plan` is the shape all of those
fit into (A-01 in PLAN.md).

Everything downstream benefits: dry-run output is uniform across providers,
``--out`` can dump a native artifact for review, :attr:`Step.undo` gives batch
creation a real rollback, :attr:`Plan.warnings` is where lossy translation
surfaces, and :meth:`Plan.to_json` serves machine-readable output.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover - a plan carries a report, it does not use one
    from .translate import TranslationReport


class StepKind(Enum):
    """What kind of action a step performs.

    Extended deliberately: a new kind means every executor must learn to run it,
    so the set stays small on purpose.
    """

    EXEC = "exec"  # run a command
    WRITE_FILE = "write_file"  # write a native artifact (domain XML, .vmx, ...)
    API_CALL = "api_call"  # call a management API
    CONNECT = "connect"  # establish a connection / authenticate


@dataclass
class Step:
    """One unit of work.

    Attributes:
        kind: What this step does.
        description: One human-readable line. Always present -- it is what
            dry-run output and progress reporting show.
        argv: Command and arguments, for :attr:`StepKind.EXEC`.
        path: Destination path, for :attr:`StepKind.WRITE_FILE`.
        content: File contents, for :attr:`StepKind.WRITE_FILE`.
        request: Request description, for :attr:`StepKind.API_CALL`.
        undo: A step that reverses this one, where one exists. This is what
            makes rollback possible rather than aspirational.
        destructive: True when running this step can lose data.
    """

    kind: StepKind
    description: str
    argv: Optional[List[str]] = None
    path: Optional[Path] = None
    content: Optional[str] = None
    request: Optional[Dict[str, Any]] = None
    undo: Optional["Step"] = None
    destructive: bool = False

    def __post_init__(self) -> None:
        if self.kind is StepKind.EXEC and not self.argv:
            raise ValueError("an EXEC step needs argv")
        if self.kind is StepKind.WRITE_FILE and self.path is None:
            raise ValueError("a WRITE_FILE step needs a path")
        if self.kind is StepKind.API_CALL and self.request is None:
            raise ValueError("an API_CALL step needs a request")
        if not self.description:
            raise ValueError("every step needs a description")

    def render(self) -> str:
        """Return the one-line form shown to a user."""
        if self.kind is StepKind.EXEC:
            assert self.argv is not None
            return " ".join(self.argv)
        if self.kind is StepKind.WRITE_FILE:
            size = len(self.content or "")
            return f"write {self.path} ({size} bytes)"
        if self.kind is StepKind.API_CALL:
            assert self.request is not None
            method = self.request.get("method", "CALL")
            target = self.request.get("path", self.request.get("url", ""))
            return f"{method} {target}"
        return self.description

    def shell(self) -> str:
        """Return an EXEC step as a properly quoted shell command.

        Raises:
            ValueError: If this step is not an EXEC step.
        """
        if self.kind is not StepKind.EXEC or self.argv is None:
            raise ValueError(f"{self.kind.value} steps have no shell form")
        return " ".join(shlex.quote(a) for a in self.argv)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable description of this step."""
        data: Dict[str, Any] = {
            "kind": self.kind.value,
            "description": self.description,
            "destructive": self.destructive,
        }
        if self.argv is not None:
            data["argv"] = list(self.argv)
        if self.path is not None:
            data["path"] = str(self.path)
        if self.content is not None:
            data["content"] = self.content
        if self.request is not None:
            data["request"] = self.request
        if self.undo is not None:
            data["undo"] = self.undo.to_dict()
        return data


@dataclass
class Plan:
    """An ordered set of steps, plus anything the user should be told.

    Attributes:
        provider: Which provider produced this plan.
        steps: The work, in order.
        warnings: Things that were substituted, dropped or could not be applied.
            Silent lossy translation is the failure mode that makes a
            multi-hypervisor tool untrustworthy, so it has a first-class home.
    """

    provider: str
    steps: List[Step] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    #: What did not carry over exactly, as data rather than prose -- the same
    #: findings ``warnings`` renders for a person. A caller that has to act on them
    #: needs the fields: ``apply`` checks a VM against its file afterwards, and a
    #: setting the provider *said* it could not express is explained rather than an
    #: unexplained failure to converge.
    report: Optional["TranslationReport"] = None

    def __len__(self) -> int:
        return len(self.steps)

    def __iter__(self):
        return iter(self.steps)

    def __bool__(self) -> bool:
        return bool(self.steps)

    def add(self, step: Step) -> "Plan":
        """Append a step and return self, so calls can be chained."""
        self.steps.append(step)
        return self

    def exec(self, argv: List[str], description: str = "", **kwargs: Any) -> "Plan":
        """Append an EXEC step.

        Args:
            argv: Command and arguments.
            description: Human-readable summary; defaults to the command itself.
            **kwargs: Passed to :class:`Step` (``undo``, ``destructive``).
        """
        return self.add(
            Step(
                kind=StepKind.EXEC,
                description=description or " ".join(argv),
                argv=argv,
                **kwargs,
            )
        )

    def warn(self, message: str) -> "Plan":
        """Record something the user should know about this plan."""
        self.warnings.append(message)
        return self

    @property
    def destructive(self) -> bool:
        """True if any step in the plan can lose data."""
        return any(step.destructive for step in self.steps)

    def render(self, numbered: bool = True) -> str:
        """Return the plan as text, one step per line."""
        lines = []
        for i, step in enumerate(self.steps, 1):
            prefix = f"  {i:3d}: " if numbered else "  "
            lines.append(f"{prefix}{step.render()}")
        return "\n".join(lines)

    def as_script(self) -> str:
        """Return the plan as a shell script.

        Only meaningful for providers whose steps are commands and file writes.

        Raises:
            ValueError: If the plan contains a step with no shell equivalent.
        """
        lines = ["#!/bin/sh", "set -e", ""]
        for step in self.steps:
            lines.append(f"# {step.description}")
            if step.kind is StepKind.EXEC:
                lines.append(step.shell())
            elif step.kind is StepKind.WRITE_FILE:
                lines.append(f"cat > {shlex.quote(str(step.path))} <<'VMCTL_EOF'")
                lines.append(step.content or "")
                lines.append("VMCTL_EOF")
            else:
                raise ValueError(f"{step.kind.value} steps cannot be expressed as a shell command")
            lines.append("")
        return "\n".join(lines)

    def native_artifacts(self) -> Dict[Path, str]:
        """Return the files this plan would write, keyed by destination."""
        return {
            step.path: step.content or ""
            for step in self.steps
            if step.kind is StepKind.WRITE_FILE and step.path is not None
        }

    def as_argv_lists(self) -> List[List[str]]:
        """Return the EXEC steps as argv lists.

        A compatibility shim for callers written against the old return type.
        Steps that are not commands are skipped, so this is lossy for any
        provider that writes files or calls an API -- prefer iterating steps.
        """
        return [list(s.argv) for s in self.steps if s.kind is StepKind.EXEC and s.argv]

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable description of the whole plan."""
        return {
            "provider": self.provider,
            "steps": [s.to_dict() for s in self.steps],
            "warnings": list(self.warnings),
            "destructive": self.destructive,
        }

    def to_json(self, indent: int = 2) -> str:
        """Return the plan as JSON."""
        return json.dumps(self.to_dict(), indent=indent)
