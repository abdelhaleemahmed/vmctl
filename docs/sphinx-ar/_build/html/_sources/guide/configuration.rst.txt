مرجع إعدادات الجهاز الافتراضي
================================

كل جهاز افتراضي في vmctl هو ملف YAML أو JSON عادي. تصف هذه الصفحة جميع الحقول المدعومة.

مثال بسيط
----------

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

الحقول الرئيسية
----------------

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - الحقل
     - القيمة الافتراضية
     - الوصف
   * - ``name``
     - *مطلوب*
     - اسم الجهاز الافتراضي — يجب أن يكون فريداً في VirtualBox.
   * - ``ostype``
     - ``Ubuntu_64``
     - معرّف نوع نظام التشغيل في VirtualBox (مثال: ``Ubuntu_64``، ``Windows10_64``).
   * - ``description``
     - ``null``
     - وصف اختياري.
   * - ``audio_enabled``
     - ``false``
     - تفعيل الصوت.
   * - ``usb_enabled``
     - ``false``
     - تفعيل وحدة تحكم USB.
   * - ``rtc_utc``
     - ``true``
     - استخدام توقيت UTC للساعة الداخلية.
   * - ``metadata``
     - ``{}``
     - بيانات وصفية اختيارية (لا تُرسل إلى VirtualBox).

إعدادات المعالج (cpu)
----------------------

.. code-block:: yaml

   cpu:
     count: 4
     execution_cap: 80
     nested_virt: true

.. list-table::
   :header-rows: 1

   * - الحقل
     - الافتراضي
     - القيود
     - الوصف
   * - ``count``
     - 2
     - 1–128
     - عدد المعالجات الافتراضية.
   * - ``execution_cap``
     - 100
     - 1–100
     - الحد الأقصى لاستهلاك المعالج (%).
   * - ``hotplug``
     - false
     -
     - دعم الإضافة الساخنة للمعالجات.
   * - ``pae``
     - false
     -
     - تمديد العنونة الفيزيائية (للأنظمة 32 بت).
   * - ``nested_virt``
     - false
     -
     - الافتراضية المتداخلة (تشغيل KVM داخل VirtualBox).

إعدادات الذاكرة (memory)
--------------------------

.. code-block:: yaml

   memory:
     mb: 8192
     vram_mb: 128

.. list-table::
   :header-rows: 1

   * - الحقل
     - الافتراضي
     - القيود
     - الوصف
   * - ``mb``
     - 2048
     - 4–1,048,576
     - حجم الذاكرة العشوائية بالميغابايت.
   * - ``vram_mb``
     - 16
     - 1–256
     - حجم ذاكرة الفيديو بالميغابايت.

إعدادات البرنامج الثابت (firmware)
------------------------------------

.. code-block:: yaml

   firmware:
     type: EFI64
     secure_boot: false

القيم المتاحة لـ ``type``: ``BIOS``، ``EFI``، ``EFI64``، ``EFI32``.

إعدادات الأقراص (disks)
-------------------------

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

**قيم** ``type``: ``HDD`` (قرص صلب)، ``SSD`` (قرص صلب سريع)، ``DVD`` (قرص بصري)

**قيم** ``format``: ``VDI`` (صيغة VirtualBox الأصلية)، ``VMDK`` (متوافق مع VMware)،
``VHD`` (متوافق مع Hyper-V)، ``RAW`` (صورة خام)

**قيم** ``variant``: ``THIN`` (تخصيص ديناميكي)، ``THICK`` (تخصيص مسبق كامل)

إعدادات الشبكة (networks)
--------------------------

.. code-block:: yaml

   networks:
     - network_type: NAT
     - network_type: BRIDGED
       adapter_name: enp3s0
     - network_type: INTERNAL
       adapter_name: labnet

**قيم** ``network_type``:

.. list-table::
   :header-rows: 1

   * - القيمة
     - الوصف
   * - ``NAT``
     - الوصول للإنترنت عبر الجهاز المضيف — الأجهزة الافتراضية غير مرئية مباشرةً.
   * - ``BRIDGED``
     - الجهاز الافتراضي يظهر كجهاز مستقل في الشبكة الفيزيائية.
   * - ``HOSTONLY``
     - شبكة معزولة بين الجهاز المضيف والأجهزة الافتراضية فقط.
   * - ``INTERNAL``
     - شبكة معزولة بين الأجهزة الافتراضية فقط — بدون وصول للمضيف.
   * - ``NATNETWORK``
     - NAT مع DHCP مشترك لعدة أجهزة افتراضية.

الحد الأقصى 8 محولات شبكة لكل جهاز افتراضي.

إعدادات الإقلاع (boot)
-----------------------

.. code-block:: yaml

   boot:
     order: [disk, dvd, none, none]
     acpi: true
     ioapic: true

الأجهزة الصالحة للإقلاع: ``disk``، ``dvd``، ``floppy``، ``network``، ``none``.
