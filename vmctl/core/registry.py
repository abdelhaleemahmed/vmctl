"""
Provider discovery and selection.

``VMCtlEngine`` used to contain ``if provider == "virtualbox": ... else: raise``,
which made adding a hypervisor an edit to the core rather than a new package.
Providers now register themselves with a **lazy loader**, so a provider whose
Python bindings or binaries are missing costs nothing at import time and simply
reports itself unavailable (A-03 in PLAN.md).

External packages can add providers through the ``vmctl.providers`` entry-point
group without vmctl knowing about them.
"""

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from .exceptions import DependencyError, ValidationError

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from ..providers.base import BaseProvider

#: Environment variable that selects a provider when no flag is given.
ENV_VAR = "VMCTL_PROVIDER"

#: Entry-point group third-party providers advertise themselves in.
ENTRY_POINT_GROUP = "vmctl.providers"


@dataclass
class ProviderEntry:
    """One registered provider.

    Attributes:
        name: The name used on the command line.
        loader: Returns the provider class. Called only when the provider is
            actually needed, so an optional dependency stays optional.
        description: One line shown by ``vmctl providers``.
    """

    name: str
    loader: Callable[[], type]
    description: str = ""

    def load(self) -> type:
        """Import and return the provider class.

        Raises:
            DependencyError: If the provider's own dependencies are missing.
        """
        try:
            return self.loader()
        except ImportError as exc:
            raise DependencyError(
                f"the {self.name} provider is not installed",
                dependency=self.name,
                install_command=f"pip install 'vmctl[{self.name}]'",
                original_exception=exc,
            )


_REGISTRY: Dict[str, ProviderEntry] = {}
_ENTRY_POINTS_LOADED = False


def register(name: str, loader: Callable[[], type], description: str = "") -> None:
    """Register a provider under *name*.

    Args:
        name: Command-line name, e.g. ``"virtualbox"``.
        loader: Callable returning the provider class. Keep the import inside it.
        description: One line for ``vmctl providers``.
    """
    _REGISTRY[name] = ProviderEntry(name=name, loader=loader, description=description)


def _load_entry_points() -> None:
    """Add providers advertised by installed packages, once."""
    global _ENTRY_POINTS_LOADED
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED = True
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover - Python < 3.8
        return
    try:
        found = entry_points()
        group = (
            found.select(group=ENTRY_POINT_GROUP)
            if hasattr(found, "select")
            else found.get(ENTRY_POINT_GROUP, [])
        )
    except Exception:  # pragma: no cover - a broken distribution must not break vmctl
        return
    for entry in group:
        if entry.name in _REGISTRY:
            continue
        register(entry.name, entry.load, description=f"from {entry.value}")


def names() -> List[str]:
    """Return every registered provider name, sorted."""
    _load_entry_points()
    return sorted(_REGISTRY)


def entries() -> List[ProviderEntry]:
    """Return every registered provider, sorted by name."""
    _load_entry_points()
    return [_REGISTRY[n] for n in sorted(_REGISTRY)]


def provider_class(name: str) -> type:
    """Return a provider class by name.

    Args:
        name: Registered provider name.

    Returns:
        The provider class.

    Raises:
        ValidationError: If no provider is registered under that name.
        DependencyError: If the provider's dependencies are missing.
    """
    _load_entry_points()
    entry = _REGISTRY.get(name)
    if entry is None:
        raise ValidationError(
            f"Unknown provider {name!r}",
            field="provider",
            value=name,
            expected=" | ".join(names()),
        )
    return entry.load()


def create(name: str) -> "BaseProvider":
    """Instantiate a provider by name.

    Args:
        name: Registered provider name.

    Returns:
        A provider instance.
    """
    backend: "BaseProvider" = provider_class(name)()
    return backend


def is_available(name: str) -> bool:
    """Whether a provider can be used on this machine.

    A provider is available when its dependencies import and its own
    availability check passes. Anything unexpected counts as unavailable: this
    is used to *choose* a provider, so it must never raise.

    Args:
        name: Registered provider name.
    """
    try:
        cls = provider_class(name)
        check = getattr(cls, "is_available", None)
        return bool(check()) if callable(check) else True
    except Exception:
        return False


def detect(preferred: Optional[List[str]] = None) -> Optional[str]:
    """Return the first provider that is usable here.

    Args:
        preferred: Order to try. Defaults to registration order with
            VirtualBox first, so behaviour does not change on a machine that has
            both.

    Returns:
        A provider name, or None when nothing is usable.
    """
    order = preferred or (["virtualbox"] + [n for n in names() if n != "virtualbox"])
    for name in order:
        if name in _REGISTRY and is_available(name):
            return name
    return None


def resolve(requested: Optional[str] = None) -> str:
    """Decide which provider to use.

    Precedence: an explicit name, then ``VMCTL_PROVIDER``, then detection, then
    VirtualBox as the historical default.

    Args:
        requested: Name passed on the command line, if any.

    Returns:
        The provider name to use.

    Raises:
        ValidationError: If an explicitly requested provider is not registered.
    """
    if requested:
        provider_class(requested)  # raises if unknown
        return requested
    from_env = os.environ.get(ENV_VAR)
    if from_env:
        provider_class(from_env)
        return from_env
    return detect() or "virtualbox"
