VM Configuration Reference
==========================

Every vmctl VM is a plain YAML (or JSON) file.  This page describes every
supported field.

Minimal example
---------------

.. code-block:: yaml

   name: minimal-linux
   guest_os: ubuntu22.04

   cpu:
     count: 2

   memory:
     mb: 2048

   storage:
     - name: system
       size_mb: 20480
       bus: sata
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
   * - ``guest_os``
     - ``ubuntu``
     - Which OS the guest runs, as a short neutral id: ``ubuntu22.04``,
       ``debian12``, ``rhel9``, ``win11``, ``freebsd``, ``other`` and so on --
       the ids libosinfo uses, so virt-install and GNOME Boxes understand the same
       names. Each provider translates: VirtualBox creates ``Ubuntu22_LTS_64``,
       libvirt records a libosinfo id in the domain's metadata. A hypervisor's own
       string (``Ubuntu_64``) is accepted and passed through untranslated, which
       is what keeps 1.1.x files working; ``ostype:`` is still accepted as the
       older name for this field.
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

storage
-------

Disks, optical drives and floppy drives are all devices on a bus, so they are one
list:

.. code-block:: yaml

   storage:
     - name: system
       size_mb: 51200
       bus: sata
       bootable: true
       nonrotational: true      # present it to the guest as solid-state
     - name: installer
       kind: cdrom
       bus: ide
       source: /isos/ubuntu.iso

**kind** values: ``disk``, ``cdrom``, ``floppy``. A ``cdrom`` or ``floppy`` is
*removable*: its medium is inserted with ``source``, never created, and it has no
``size_mb``.

**bus** values: ``ide``, ``sata``, ``scsi``, ``sas``, ``nvme``, ``virtio-blk``,
``virtio-scsi``, ``usb``, ``floppy``. Which of these a hypervisor has, and which
kinds each will carry, is declared per provider — ``vmctl providers`` names them,
and asking for one that is not there is refused with the list that is.

**format** values: ``vdi``, ``vmdk``, ``vhd``, ``vhdx``, ``qcow2``, ``qed``,
``parallels``, ``raw``. **Leave it out** unless you care: the provider then uses
whichever format it creates natively, which is what keeps one file usable on more
than one hypervisor. Providers differ in what they can create as against merely
read — VirtualBox 7.1 can attach a VHDX but not create one.

**allocation** values: ``thin`` (grows on demand), ``thick`` (preallocated). Some
formats allow only one, and vmctl says so rather than failing inside the
hypervisor.

Other device fields:

.. list-table::
   :header-rows: 1
   :widths: 20 10 55

   * - Field
     - Default
     - Description
   * - ``controller``
     - *unset*
     - Which controller to attach to — a ``storage_controllers`` entry's ``id``,
       or the hypervisor's own name for one. Unset means "whichever controller
       serves ``bus``", and vmctl creates one if the config declares none.
   * - ``slot`` / ``unit``
     - *unset*
     - Position on the controller. Unset means vmctl allocates the lowest free
       one, the same way every time.
   * - ``source``
     - ``null``
     - An existing image to attach instead of creating one: an ISO for a drive,
       or a disk image that is already there.
   * - ``readonly``
     - ``false``
     - Attach without write access.
   * - ``discard``
     - ``false``
     - Pass the guest's TRIM/UNMAP through, so freeing space inside the guest
       frees it on the host.
   * - ``hotpluggable``
     - ``false``
     - Let the guest detach the device while running. Only some buses accept
       this; vmctl reports it when the one you chose does not.
   * - ``provider_options``
     - ``{}``
     - Settings only one hypervisor has, carried verbatim.

storage_controllers
-------------------

Usually unnecessary — a device that names a ``bus`` gets a controller made for it.
Declare them to control the port count, or to have several on one bus:

.. code-block:: yaml

   storage_controllers:
     - id: sata0            # what devices reference
       bus: sata
       port_count: 4
       bootable: true
       native_name: SATA Controller   # what this hypervisor calls it

``id`` is the portable half and ``native_name`` the hypervisor's own; keeping them
apart is what lets the same file describe the same layout on another hypervisor.

.. note::

   Files written for vmctl 1.1.x keep loading unchanged. ``disks:`` is still
   accepted for ``storage:``, along with ``type:`` (``hdd``/``ssd``/``dvd``),
   ``variant:``, ``controller:`` naming a bus, ``controller_name:``, ``port:`` and
   ``device:``. Saving such a file writes the current names.

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
