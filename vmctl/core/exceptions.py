"""
Exception hierarchy for vmctl.

Every class here has a caller. The module used to define twenty classes and four
helpers of which seventeen and three were unused, while the code raised bare
``ProviderError("...")`` strings everywhere; two of the unused ones
(``PermissionError``, ``TimeoutError``) shadowed builtins, so ``except
TimeoutError`` inside this package silently caught the wrong thing (H-08).

Errors carry the field at fault, what was expected and a recovery hint. The CLI
renders all of that -- see ``vmctl.cli.main._fail`` -- so raising a specific
class with context is what makes a good error message, not extra printing at the
call site.
"""

from typing import Any, Dict, List, Optional
from enum import Enum


class ErrorSeverity(Enum):
    """Error severity levels"""

    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    INFO = "info"


class VMToolError(Exception):
    """
    Base exception for all vmtool errors.
    Includes context, severity, and recovery hints.
    """

    def __init__(
        self,
        message: str,
        *,
        severity: ErrorSeverity = ErrorSeverity.ERROR,
        context: Optional[Dict[str, Any]] = None,
        recovery_hint: Optional[str] = None,
        original_exception: Optional[Exception] = None,
    ):
        self.message = message
        self.severity = severity
        self.context = context or {}
        self.recovery_hint = recovery_hint
        self.original_exception = original_exception
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        """Format error message with context"""
        parts = [f"[{self.severity.value.upper()}] {self.message}"]

        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            parts.append(f"Context: {context_str}")

        if self.recovery_hint:
            parts.append(f"Hint: {self.recovery_hint}")

        if self.original_exception:
            parts.append(
                f"Original error: {type(self.original_exception).__name__}: "
                f"{self.original_exception}"
            )

        return "\n".join(parts)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for serialization"""
        return {
            "type": self.__class__.__name__,
            "message": self.message,
            "severity": self.severity.value,
            "context": self.context,
            "recovery_hint": self.recovery_hint,
            "original_exception": str(self.original_exception) if self.original_exception else None,
        }


class ValidationError(VMToolError):
    """
    Raised when VM configuration validation fails.

    Examples:
    - Invalid CPU count
    - Insufficient memory
    - Missing required fields
    - Unsupported features for provider
    """

    def __init__(
        self,
        message: str,
        *,
        field: Optional[str] = None,
        value: Any = None,
        expected: Any = None,
        constraints: Optional[List[str]] = None,
        **kwargs,
    ):
        # Build context from validation-specific info
        context = kwargs.pop("context", {})
        if field:
            context["field"] = field
        if value is not None:
            context["actual_value"] = value
        if expected is not None:
            context["expected_value"] = expected
        if constraints:
            context["constraints"] = constraints

        # Add recovery hint if not provided
        if "recovery_hint" not in kwargs:
            if field and expected is not None:
                kwargs["recovery_hint"] = f"Check the '{field}' field. Expected: {expected}"
            elif constraints:
                kwargs["recovery_hint"] = f"Constraints: {', '.join(constraints)}"

        super().__init__(message, context=context, **kwargs)
        self.field = field
        self.value = value
        self.expected = expected
        self.constraints = constraints or []


class SerializationError(VMToolError):
    """
    Raised when serialization/deserialization fails.

    Examples:
    - Invalid JSON/YAML syntax
    - File permission issues
    - Encoding problems
    """

    def __init__(
        self,
        message: str,
        *,
        file_path: Optional[str] = None,
        format: Optional[str] = None,
        line: Optional[int] = None,
        column: Optional[int] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        if file_path:
            context["file_path"] = file_path
        if format:
            context["format"] = format
        if line is not None:
            context["line"] = line
        if column is not None:
            context["column"] = column

        super().__init__(message, context=context, **kwargs)
        self.file_path = file_path
        self.format = format
        self.line = line
        self.column = column


class ProviderError(VMToolError):
    """
    Raised when provider operations fail.

    Examples:
    - VirtualBox not installed
    - VBoxManage command failed
    - VM not found
    - Insufficient permissions
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "virtualbox",
        operation: Optional[str] = None,
        command: Optional[List[str]] = None,
        exit_code: Optional[int] = None,
        stderr: Optional[str] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        context["provider"] = provider
        if operation:
            context["operation"] = operation
        if command:
            context["command"] = " ".join(command) if isinstance(command, list) else command
        if exit_code is not None:
            context["exit_code"] = exit_code
        if stderr:
            # Truncate long error output
            context["stderr"] = stderr[:500] + "..." if len(stderr) > 500 else stderr

        super().__init__(message, context=context, **kwargs)
        self.provider = provider
        self.operation = operation
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr


class VMNotFoundError(ProviderError):
    """Raised when a requested VM does not exist"""

    def __init__(self, vm_name: str, **kwargs):
        super().__init__(
            f"Virtual Machine '{vm_name}' not found",
            operation="read_vm",
            recovery_hint=f"Check if VM '{vm_name}' exists with 'VBoxManage list vms'",
            context={"vm_name": vm_name},
            **kwargs,
        )
        self.vm_name = vm_name


class VMAlreadyExistsError(ProviderError):
    """Raised when trying to create a VM that already exists"""

    def __init__(self, vm_name: str, **kwargs):
        super().__init__(
            f"Virtual Machine '{vm_name}' already exists",
            operation="create_vm",
            recovery_hint="Use a different name or delete the existing VM first",
            context={"vm_name": vm_name},
            **kwargs,
        )
        self.vm_name = vm_name


class VMStateError(ProviderError):
    """Raised for invalid VM state operations"""

    def __init__(
        self, message: str, *, vm_name: str, current_state: str, required_state: str, **kwargs
    ):
        super().__init__(
            message,
            operation="change_state",
            recovery_hint=f"VM must be in '{required_state}' state, but is in '{current_state}'",
            context={
                "vm_name": vm_name,
                "current_state": current_state,
                "required_state": required_state,
            },
            **kwargs,
        )
        self.vm_name = vm_name
        self.current_state = current_state
        self.required_state = required_state


class BatchError(VMToolError):
    """
    Raised for batch processing errors.

    Examples:
    - Invalid batch definition
    - Batch file not found
    - Batch processing interrupted
    """

    def __init__(
        self,
        message: str,
        *,
        batch_file: Optional[str] = None,
        instance_index: Optional[int] = None,
        instance_name: Optional[str] = None,
        total_instances: Optional[int] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        if batch_file:
            context["batch_file"] = batch_file
        if instance_index is not None:
            context["instance_index"] = instance_index
        if instance_name:
            context["instance_name"] = instance_name
        if total_instances is not None:
            context["total_instances"] = total_instances

        # Add recovery hint for batch errors
        if "recovery_hint" not in kwargs:
            if instance_index is not None and instance_name:
                kwargs["recovery_hint"] = (
                    f"Error occurred at instance {instance_index} ('{instance_name}'). "
                    f"Check the batch definition at this position."
                )

        super().__init__(message, context=context, **kwargs)
        self.batch_file = batch_file
        self.instance_index = instance_index
        self.instance_name = instance_name
        self.total_instances = total_instances


class DependencyError(VMToolError):
    """
    Raised when required dependencies are missing.

    Examples:
    - VirtualBox not installed
    - VBoxManage not in PATH
    - Python package missing
    """

    def __init__(
        self,
        message: str,
        *,
        dependency: Optional[str] = None,
        install_command: Optional[str] = None,
        version_required: Optional[str] = None,
        version_found: Optional[str] = None,
        **kwargs,
    ):
        context = kwargs.pop("context", {})
        if dependency:
            context["dependency"] = dependency
        if install_command:
            context["install_command"] = install_command
        if version_required:
            context["version_required"] = version_required
        if version_found:
            context["version_found"] = version_found

        # Add recovery hint for missing dependencies
        if "recovery_hint" not in kwargs and install_command:
            kwargs["recovery_hint"] = f"Try installing with: {install_command}"

        super().__init__(message, context=context, **kwargs)
        self.dependency = dependency
        self.install_command = install_command
        self.version_required = version_required
        self.version_found = version_found


def get_error_summary(error: VMToolError) -> str:
    """
    Get a one-line summary of an error.

    Args:
        error: The error to summarize

    Returns:
        One-line error summary
    """
    if isinstance(error, ValidationError):
        return f"Validation failed: {error.message}"
    elif isinstance(error, ProviderError):
        return f"Provider error: {error.message}"
    elif isinstance(error, SerializationError):
        return f"Serialization error: {error.message}"
    elif isinstance(error, BatchError):
        return f"Batch error: {error.message}"
    else:
        return f"Error: {error.message}"
