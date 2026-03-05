VM Configuration Reference
==========================

Every vmctl VM is a plain YAML (or JSON) file.  This page describes every
supported field.

Minimal example
---------------

.. code-block:: yaml

   name: minimal-linux
   ostype: Ubuntu_64

   cpu:
     count: 2

   memory:
     mb: 2048

   disks:
     - name: system
       size_mb: 20480
       type: HDD
       controller: SATA
       port: 0
       bootable: true

   networks:
     - network_type: NAT

   firmware:
     type: BIOS

   boot:
     order: [disk, dvd, none, none]

Top-level fields
----------------

.. list-table::
   :header-rows: 1
   :widths: 20 10 50

   * - Field
     - Default
     - Description
   * - ``name``
     - *required*
     - VM name — must be unique in VirtualBox.
   * - ``ostype``
     - ``Ubuntu_64``
     - VirtualBox OS type identifier (e.g. ``Ubuntu_64``, ``Windows10_64``).
   * - ``description``
     - ``null``
     - Optional free-text description.
   * - ``audio_enabled``
     - ``false``
     - Enable the audio device.
   * - ``usb_enabled``
     - ``false``
     - Enable the USB controller.
   * - ``rtc_utc``
     - ``true``
     - Use UTC for the hardware clock.
   * - ``metadata``
     - ``{}``
     - Arbitrary key-value annotations (not sent to VirtualBox).

cpu
---

.. code-block:: yaml

   cpu:
     count: 4
     execution_cap: 80
     nested_virt: true

.. list-table::
   :header-rows: 1

   * - Field
     - Default
     - Constraints
     - Description
   * - ``count``
     - 2
     - 1–128
     - Number of virtual CPUs.
   * - ``execution_cap``
     - 100
     - 1–100
     - CPU usage cap (%).
   * - ``hotplug``
     - false
     -
     - CPU hotplug support.
   * - ``pae``
     - false
     -
     - Physical Address Extension.
   * - ``nested_virt``
     - false
     -
     - Nested virtualisation (KVM inside VirtualBox).

memory
------

.. code-block:: yaml

   memory:
     mb: 8192
     vram_mb: 128

.. list-table::
   :header-rows: 1

   * - Field
     - Default
     - Constraints
     - Description
   * - ``mb``
     - 2048
     - 4–1,048,576
     - RAM in megabytes.
   * - ``vram_mb``
     - 16
     - 1–256
     - Video RAM in megabytes.

firmware
--------

.. code-block:: yaml

   firmware:
     type: EFI64
     secure_boot: false

Valid ``type`` values: ``BIOS``, ``EFI``, ``EFI64``, ``EFI32``.

disks
-----

.. code-block:: yaml

   disks:
     - name: system
       size_mb: 51200
       type: HDD
       format: VDI
       variant: THIN
       controller: SATA
       port: 0
       device: 0
       bootable: true

**type** values: ``HDD``, ``SSD``, ``DVD``

**format** values: ``VDI``, ``VMDK``, ``VHD``, ``RAW``

**variant** values: ``THIN`` (dynamic), ``THICK`` (fixed)

networks
--------

.. code-block:: yaml

   networks:
     - network_type: NAT
     - network_type: BRIDGED
       adapter_name: enp3s0

**network_type** values: ``NAT``, ``BRIDGED``, ``HOSTONLY``, ``INTERNAL``, ``NATNETWORK``

Up to 8 adapters per VM.

boot
----

.. code-block:: yaml

   boot:
     order: [disk, dvd, none, none]
     acpi: true
     ioapic: true

Valid boot devices: ``disk``, ``dvd``, ``floppy``, ``network``, ``none``.
