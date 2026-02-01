"""
Complete exception hierarchy for vmtool
All exceptions are properly documented and include context
"""

from typing import Optional, Dict, Any, List
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
        original_exception: Optional[Exception] = None
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
            parts.append(f"Original error: {type(self.original_exception).__name__}: {self.original_exception}")
        
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
            "original_exception": str(self.original_exception) if self.original_exception else None
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
        **kwargs
    ):
        # Build context from validation-specific info
        context = kwargs.pop('context', {})
        if field:
            context['field'] = field
        if value is not None:
            context['actual_value'] = value
        if expected is not None:
            context['expected_value'] = expected
        if constraints:
            context['constraints'] = constraints
        
        # Add recovery hint if not provided
        if 'recovery_hint' not in kwargs:
            if field and expected is not None:
                kwargs['recovery_hint'] = f"Check the '{field}' field. Expected: {expected}"
            elif constraints:
                kwargs['recovery_hint'] = f"Constraints: {', '.join(constraints)}"
        
        super().__init__(message, context=context, **kwargs)
        self.field = field
        self.value = value
        self.expected = expected
        self.constraints = constraints or []


class SchemaValidationError(ValidationError):
    """Raised for JSON/YAML schema validation errors"""
    pass


class ProviderValidationError(ValidationError):
    """Raised for provider-specific validation errors"""
    pass


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
        **kwargs
    ):
        context = kwargs.pop('context', {})
        if file_path:
            context['file_path'] = file_path
        if format:
            context['format'] = format
        if line is not None:
            context['line'] = line
        if column is not None:
            context['column'] = column
        
        super().__init__(message, context=context, **kwargs)
        self.file_path = file_path
        self.format = format
        self.line = line
        self.column = column


class JSONSerializationError(SerializationError):
    """Raised specifically for JSON serialization errors"""
    pass


class YAMLSerializationError(SerializationError):
    """Raised specifically for YAML serialization errors"""
    pass


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
        **kwargs
    ):
        context = kwargs.pop('context', {})
        context['provider'] = provider
        if operation:
            context['operation'] = operation
        if command:
            context['command'] = ' '.join(command) if isinstance(command, list) else command
        if exit_code is not None:
            context['exit_code'] = exit_code
        if stderr:
            # Truncate long error output
            context['stderr'] = stderr[:500] + "..." if len(stderr) > 500 else stderr
        
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
            **kwargs
        )
        self.vm_name = vm_name


class VMAlreadyExistsError(ProviderError):
    """Raised when trying to create a VM that already exists"""
    
    def __init__(self, vm_name: str, **kwargs):
        super().__init__(
            f"Virtual Machine '{vm_name}' already exists",
            operation="create_vm",
            recovery_hint=f"Use a different name or delete the existing VM first",
            context={"vm_name": vm_name},
            **kwargs
        )
        self.vm_name = vm_name


class VMStateError(ProviderError):
    """Raised for invalid VM state operations"""
    
    def __init__(
        self,
        message: str,
        *,
        vm_name: str,
        current_state: str,
        required_state: str,
        **kwargs
    ):
        super().__init__(
            message,
            operation="change_state",
            recovery_hint=f"VM must be in '{required_state}' state, but is in '{current_state}'",
            context={
                "vm_name": vm_name,
                "current_state": current_state,
                "required_state": required_state
            },
            **kwargs
        )
        self.vm_name = vm_name
        self.current_state = current_state
        self.required_state = required_state


class ConfigurationError(VMToolError):
    """
    Raised for configuration-related errors.
    
    Examples:
    - Missing required configuration
    - Invalid configuration values
    - Configuration file not found
    """
    
    def __init__(
        self,
        message: str,
        *,
        config_key: Optional[str] = None,
        config_file: Optional[str] = None,
        section: Optional[str] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        if config_key:
            context['config_key'] = config_key
        if config_file:
            context['config_file'] = config_file
        if section:
            context['section'] = section
        
        super().__init__(message, context=context, **kwargs)
        self.config_key = config_key
        self.config_file = config_file
        self.section = section


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
        **kwargs
    ):
        context = kwargs.pop('context', {})
        if batch_file:
            context['batch_file'] = batch_file
        if instance_index is not None:
            context['instance_index'] = instance_index
        if instance_name:
            context['instance_name'] = instance_name
        if total_instances is not None:
            context['total_instances'] = total_instances
        
        # Add recovery hint for batch errors
        if 'recovery_hint' not in kwargs:
            if instance_index is not None and instance_name:
                kwargs['recovery_hint'] = (
                    f"Error occurred at instance {instance_index} ('{instance_name}'). "
                    f"Check the batch definition at this position."
                )
        
        super().__init__(message, context=context, **kwargs)
        self.batch_file = batch_file
        self.instance_index = instance_index
        self.instance_name = instance_name
        self.total_instances = total_instances


class DiskError(VMToolError):
    """
    Raised for disk-related errors.
    
    Examples:
    - Insufficient disk space
    - Invalid disk format
    - Disk creation failed
    """
    
    def __init__(
        self,
        message: str,
        *,
        disk_name: Optional[str] = None,
        disk_size: Optional[int] = None,
        disk_path: Optional[str] = None,
        available_space: Optional[int] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        if disk_name:
            context['disk_name'] = disk_name
        if disk_size is not None:
            context['disk_size_mb'] = disk_size
        if disk_path:
            context['disk_path'] = disk_path
        if available_space is not None:
            context['available_space_mb'] = available_space
        
        # Add recovery hint for disk space issues
        if 'recovery_hint' not in kwargs and available_space is not None and disk_size is not None:
            if available_space < disk_size:
                kwargs['recovery_hint'] = (
                    f"Insufficient disk space. Required: {disk_size}MB, "
                    f"Available: {available_space}MB"
                )
        
        super().__init__(message, context=context, **kwargs)
        self.disk_name = disk_name
        self.disk_size = disk_size
        self.disk_path = disk_path
        self.available_space = available_space


class NetworkError(VMToolError):
    """
    Raised for network-related errors.
    
    Examples:
    - Invalid network configuration
    - Network adapter not found
    - IP address conflict
    """
    
    def __init__(
        self,
        message: str,
        *,
        adapter_name: Optional[str] = None,
        network_type: Optional[str] = None,
        mac_address: Optional[str] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        if adapter_name:
            context['adapter_name'] = adapter_name
        if network_type:
            context['network_type'] = network_type
        if mac_address:
            context['mac_address'] = mac_address
        
        super().__init__(message, context=context, **kwargs)
        self.adapter_name = adapter_name
        self.network_type = network_type
        self.mac_address = mac_address


class PermissionError(VMToolError):
    """
    Raised for permission-related errors.
    
    Examples:
    - Insufficient permissions to run VBoxManage
    - Cannot write to disk
    - Cannot read configuration file
    """
    
    def __init__(
        self,
        message: str,
        *,
        path: Optional[str] = None,
        required_permission: Optional[str] = None,
        user: Optional[str] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        if path:
            context['path'] = path
        if required_permission:
            context['required_permission'] = required_permission
        if user:
            context['user'] = user
        
        # Add recovery hint for permission errors
        if 'recovery_hint' not in kwargs and path and required_permission:
            kwargs['recovery_hint'] = (
                f"Check permissions for '{path}'. "
                f"Required: {required_permission}"
            )
        
        super().__init__(message, context=context, **kwargs)
        self.path = path
        self.required_permission = required_permission
        self.user = user


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
        **kwargs
    ):
        context = kwargs.pop('context', {})
        if dependency:
            context['dependency'] = dependency
        if install_command:
            context['install_command'] = install_command
        if version_required:
            context['version_required'] = version_required
        if version_found:
            context['version_found'] = version_found
        
        # Add recovery hint for missing dependencies
        if 'recovery_hint' not in kwargs and install_command:
            kwargs['recovery_hint'] = f"Try installing with: {install_command}"
        
        super().__init__(message, context=context, **kwargs)
        self.dependency = dependency
        self.install_command = install_command
        self.version_required = version_required
        self.version_found = version_found


class TimeoutError(VMToolError):
    """
    Raised when operations time out.
    
    Examples:
    - VM creation timeout
    - VM shutdown timeout
    - Network operation timeout
    """
    
    def __init__(
        self,
        message: str,
        *,
        timeout_seconds: Optional[int] = None,
        operation: Optional[str] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        if timeout_seconds is not None:
            context['timeout_seconds'] = timeout_seconds
        if operation:
            context['operation'] = operation
        
        super().__init__(
            message,
            severity=ErrorSeverity.WARNING,
            recovery_hint="Try increasing the timeout or checking system resources",
            context=context,
            **kwargs
        )
        self.timeout_seconds = timeout_seconds
        self.operation = operation


class ResourceExhaustedError(VMToolError):
    """
    Raised when system resources are exhausted.
    
    Examples:
    - Out of memory
    - Disk full
    - Too many VMs running
    """
    
    def __init__(
        self,
        message: str,
        *,
        resource_type: str,
        limit: Optional[int] = None,
        current_usage: Optional[int] = None,
        **kwargs
    ):
        context = kwargs.pop('context', {})
        context['resource_type'] = resource_type
        if limit is not None:
            context['limit'] = limit
        if current_usage is not None:
            context['current_usage'] = current_usage
        
        # Add recovery hint
        if 'recovery_hint' not in kwargs:
            if limit is not None and current_usage is not None:
                kwargs['recovery_hint'] = (
                    f"Resource {resource_type} exhausted. "
                    f"Limit: {limit}, Current: {current_usage}"
                )
            else:
                kwargs['recovery_hint'] = f"Free up {resource_type} resources and try again"
        
        super().__init__(message, context=context, **kwargs)
        self.resource_type = resource_type
        self.limit = limit
        self.current_usage = current_usage


# Utility functions for working with exceptions
def create_error_context(**kwargs) -> Dict[str, Any]:
    """Create a standardized error context dictionary"""
    return kwargs


def wrap_exception(
    exception: Exception,
    wrapper_class,
    message: Optional[str] = None,
    **kwargs
) -> VMToolError:
    """
    Wrap a generic exception in a vmtool exception.
    
    Args:
        exception: Original exception to wrap
        wrapper_class: VMToolError subclass to wrap with
        message: Optional custom message
        **kwargs: Additional arguments for wrapper
    
    Returns:
        Wrapped exception
    """
    if message is None:
        message = str(exception)
    
    return wrapper_class(
        message=message,
        original_exception=exception,
        **kwargs
    )


def is_recoverable_error(error: VMToolError) -> bool:
    """
    Check if an error is likely recoverable.
    
    Args:
        error: The error to check
    
    Returns:
        True if the error is recoverable
    """
    # Timeouts are often recoverable
    if isinstance(error, TimeoutError):
        return True
    
    # Resource exhaustion might be recoverable if resources are freed
    if isinstance(error, ResourceExhaustedError):
        return True
    
    # Permission errors might be recoverable with proper permissions
    if isinstance(error, PermissionError):
        return True
    
    # Validation errors are recoverable by fixing the configuration
    if isinstance(error, ValidationError):
        return True
    
    # Default: assume not recoverable
    return False


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
