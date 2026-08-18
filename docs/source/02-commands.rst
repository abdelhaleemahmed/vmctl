=================
Command reference
=================

Run ``vmctl <command> --help`` for the full option list of any command.

Inspecting
==========

``vmctl list [--format table|simple]``
   List all VMs with their status.

``vmctl read <vm> [--format yaml|json]``
   Print a VM's full configuration.

``vmctl status <vm>``
   Show a single VM's power/run status.

Config-as-code
==============

``vmctl export <vm> -o <file> [--format yaml|json]``
   Export a VM's configuration to a file.

``vmctl import <file> [--new-name NAME] [--apply]``
   Create a VM from a configuration file. Dry-run unless ``--apply``.

``vmctl validate <file>``
   Validate a configuration file against the provider's capabilities.

Lifecycle
=========

``vmctl start <vm>``
   Start a VM.

``vmctl stop <vm> [--force/-f]``
   Stop a VM — graceful ACPI shutdown, or a forced power-off with ``--force``.

``vmctl edit <vm> [--new-name] [--memory MB] [--cpus N] [--disk-size MB]``
   Edit a VM's configuration in place.

``vmctl create <source_vm> --new-name NAME [--memory MB] [--cpus N] [--apply]``
   Create a new VM from an existing one (or a config file), with overrides.

``vmctl delete <vm> [--force/-f]``
   Delete a VM.

Batch
=====

``vmctl batch create <file> [--apply]``
   Create many VMs from one batch/template file. Dry-run unless ``--apply``.

``vmctl batch template [-o FILE] [--format yaml|json]``
   Write a starter batch template you can edit.
