إنشاء جهاز افتراضي بـ virsh يدوياً
====================================

تُنشئ هذه الصفحة جهازاً افتراضياً واحداً باستخدام ``virsh`` و ``qemu-img`` وحدهما،
من البداية إلى النهاية، ثم تُنشئ الجهاز نفسه بـ vmctl. وهي هنا لسببين: لترى بالضبط
ما يفعله vmctl بالنيابة عنك، ولأن ``virsh`` ما زال هو الأداة الصحيحة لكل ما لا
يُمثّله vmctl.

كل أمر وكل رسالة أدناه نُفِّذت على مضيف حقيقي --- libvirt 11.10.0 و QEMU 10.1.0
واتصال ``qemu:///session`` --- ولُصقت كما خرجت، بما في ذلك المحاولتان الفاشلتان.
الجهاز صغير عن قصد (128 ميغابايت وقرص بحجم 1 غيغابايت) لأن المضيف الذي بُني عليه
يُشغّل أجهزة أخرى؛ والشكل نفسه عند أي حجم.

.. contents:: في هذه الصفحة
   :local:
   :depth: 1

أيّ libvirt تُحدِّث؟
--------------------

يتصل ``virsh`` إمّا بـ *جلسة المستخدم* أو بـ *خدمة النظام*، ولكلٍّ منهما قائمة
نطاقات (domains) مستقلة:

.. code-block:: console

   $ virsh -c qemu:///session list --all      # نطاقاتك، بلا صلاحيات جذر
   $ virsh -c qemu:///system  list --all      # نطاقات الآلة، تحتاج صلاحيات

كل ما هنا يستخدم ``qemu:///session``. النطاق المُعرَّف في أحدهما غير مرئي من الآخر،
وهذا هو السبب المعتاد لـ"اختفاء" جهاز افتراضي. استخدم ``LIBVIRT_DEFAULT_URI`` إن
أتعبك كتابة ``-c``.

الخطوة 1 --- إنشاء القرص
------------------------

libvirt لا يُنشئ الأقراص عنك. لا شيء يُنشئ الصورة إلّا أنت:

.. code-block:: console

   $ mkdir -p ~/.local/share/libvirt/images
   $ qemu-img create -f qcow2 ~/.local/share/libvirt/images/web-01.qcow2 1G
   Formatting '/home/vagrant/.local/share/libvirt/images/web-01.qcow2', fmt=qcow2
   cluster_size=65536 extended_l2=off compression_type=zlib size=1073741824
   lazy_refcounts=off refcount_bits=16

الخطوة 2 --- كتابة وثيقة XML للنطاق
-----------------------------------

النطاق هو وثيقة XML. وهذه أصغر وثيقة مفيدة فعلاً --- قرص، ومُشغّل ضوئي، وبطاقة
شبكة، ووحدة طرفية يمكنك الوصول إليها:

.. code-block:: xml
   :caption: web-01.xml
   :linenos:

   <domain type='qemu'>
     <name>web-01</name>
     <memory unit='MiB'>128</memory>
     <currentMemory unit='MiB'>128</currentMemory>
     <vcpu>2</vcpu>
     <os>
       <type arch='x86_64' machine='q35'>hvm</type>
       <boot dev='hd'/>
       <boot dev='cdrom'/>
     </os>
     <features>
       <acpi/>
     </features>
     <cpu mode='host-model'/>
     <clock offset='utc'>
       <timer name='hpet' present='no'/>
     </clock>
     <devices>
       <emulator>/usr/libexec/qemu-kvm</emulator>
       <disk type='file' device='disk'>
         <driver name='qemu' type='qcow2'/>
         <source file='/home/vagrant/.local/share/libvirt/images/web-01.qcow2'/>
         <target dev='sda' bus='sata'/>
       </disk>
       <disk type='file' device='cdrom'>
         <driver name='qemu' type='raw'/>
         <target dev='sdb' bus='sata'/>
         <readonly/>
       </disk>
       <controller type='sata' index='0'/>
       <interface type='user'>
         <model type='virtio'/>
       </interface>
       <graphics type='vnc' port='-1'/>
       <video><model type='virtio'/></video>
       <memballoon model='virtio'/>
     </devices>
   </domain>

ثمانية وثلاثون سطراً، وعدد منها قرارات يلزمك أن تعرفها لتتخذها:

``<domain type='...'>``
   ``kvm`` للتسريع العتادي، و ``qemu`` للمحاكاة. اختر الخطأ فلن يُعرَّف النطاق ---
   انظر أدناه.

``<emulator>``
   المسار المُطلق لملف QEMU التنفيذي. وهو ``/usr/libexec/qemu-kvm`` على RHEL
   وتوزيعاتها، و ``/usr/bin/qemu-system-x86_64`` على Debian و Arch.

``<target dev='sda' bus='sata'/>``
   أنت من يُسمّي جهاز الضيف. وإذا حمل قرصان الاسم نفسه رفض libvirt الوثيقة.

``<interface type='user'>``
   شبكة بنمط المستخدم، وهي مُقابل NAT في VirtualBox. أمّا ``type='network'`` فيطلب
   شبكة libvirt موجودة فعلاً، وعلى اتصال الجلسة لا توجد عادةً واحدة.

المحاولة الأولى لا تنجح
-----------------------

كُتبت بـ ``type='kvm'`` على مضيف بلا ``/dev/kvm``:

.. code-block:: console

   $ virsh -c qemu:///session define web-01.xml
   error: Failed to define domain from web-01.xml
   error: unsupported configuration: Emulator '/usr/libexec/qemu-kvm' does not
   support virt type 'kvm'

والحل هو ``<domain type='qemu'>``. وهذا يستحق التوقف: صِحّة هذه الوثيقة تعتمد على
الآلة التي تُعرَّف عليها، فملف XML ليس قابلاً للنقل بالقدر الذي يبدو عليه. والأمر
نفسه ينطبق على مسار المُحاكي، ونوع الآلة، وأي النواقل وصيغ الصور يدعمها بناء QEMU
ذاك.

الخطوة 3 --- تعريفه
-------------------

.. code-block:: console

   $ virsh -c qemu:///session define web-01.xml
   Domain 'web-01' defined from web-01.xml

   $ virsh -c qemu:///session list --all
    Id   Name     State
   -------------------------
    -    web-01   shut off

يجعله ``define`` دائماً. أمّا ``create`` فيُشغّل نطاقاً يتلاشى عند توقفه، وهذا نادراً
ما يكون المطلوب.

الخطوة 4 --- تشغيله
-------------------

.. code-block:: console

   $ virsh -c qemu:///session start web-01
   Domain 'web-01' started

   $ virsh -c qemu:///session domstate web-01
   running

وبلا نظام مُثبّت سيبقى عند البرنامج الثابت باحثاً عن شيء يُقلع منه. ويوصلك
``virsh console web-01`` أو أي عميل VNC إلى شاشة.

ما أضافه libvirt ولم تكتبه أنت
------------------------------

الوثيقة التي تستعيدها ليست الوثيقة التي سلّمتها:

.. code-block:: console

   $ wc -l web-01.xml
   38 web-01.xml
   $ virsh -c qemu:///session dumpxml web-01 | wc -l
   146

المئة سطر الزائدة هي قرارات libvirt نفسه --- مُعرِّف UUID، وكل عنوان PCI، ولافتة
أمنية، ونوع آلة مُحوَّل إلى نسخة مُرقَّمة، ومُشغّلات افتراضية:

.. code-block:: xml

   <uuid>67e6b140-cb45-47fd-b78c-384e07872243</uuid>
   <currentMemory unit='KiB'>131072</currentMemory>
   <address type='pci' domain='0x0000' bus='0x00' slot='0x1f' function='0x2'/>
   <address type='pci' domain='0x0000' bus='0x02' slot='0x00' function='0x0'/>

وهذا مهم عند حفظ وثيقة XML في Git: الملف الذي كتبته والنطاق الموجود وثيقتان
مختلفتان، ولن يُطابق ``dumpxml`` ملفك أبداً --- فلا يمكنك مقارنتهما لترى ما تغيّر.

تغيير شيء بعد ذلك
-----------------

الذاكرة، على نطاق *قيد التشغيل*:

.. code-block:: console

   $ virsh -c qemu:///session setmaxmem web-01 512M --live
   error: Unable to change MaxMemorySize
   error: Requested operation is not valid: cannot resize the maximum memory on an
   active domain

أمّا ``--config`` فيُقبل أثناء التشغيل، ويُغيّر التعريف *المحفوظ* وحده:

.. code-block:: console

   $ virsh -c qemu:///session setmaxmem web-01 512M --config
   $ virsh -c qemu:///session dumpxml web-01 --inactive | grep '<memory'
     <memory unit='KiB'>524288</memory>
   $ virsh -c qemu:///session dumpxml web-01 | grep '<memory'
     <memory unit='KiB'>262144</memory>

جوابان مختلفان للنطاق نفسه، وبلا أي تحذير بأنهما مختلفان: التعريف الدائم يقول 512
ميغابايت، والآلة العاملة ما زالت 256. ويسري التغيير عند الإقلاع التالي. ويفتح
``virsh edit`` الوثيقة كلها في ``$EDITOR`` وفيه الانفصام نفسه.

تفكيكه
------

النطاق العامل لن تُحذف مساحته التخزينية:

.. code-block:: console

   $ virsh -c qemu:///session undefine web-01 --remove-all-storage
   error: Storage volume deletion is supported only on stopped domains

أوقفه أولاً --- ثم اقرأ السطرين التاليين بتمعّن:

.. code-block:: console

   $ virsh -c qemu:///session destroy web-01
   Domain 'web-01' destroyed

   $ virsh -c qemu:///session undefine web-01 --remove-all-storage
   error: Storage volume 'sda'(/home/vagrant/.local/share/libvirt/images/web-01.qcow2)
   is not managed by libvirt. Remove it manually.
   Domain 'web-01' has been undefined

   $ ls ~/.local/share/libvirt/images/web-01.qcow2
   /home/vagrant/.local/share/libvirt/images/web-01.qcow2

النطاق زال. **والقرص ما زال موجوداً.** لا يحذف ``--remove-all-storage`` إلّا الأحجام
الموجودة داخل مجمع تخزين لـ libvirt، والصورة التي أنشأتها بـ ``qemu-img`` في مجلدك
ليست منها. أبلغ الأمر بذلك ثم أزال تعريف النطاق على أي حال، فالسكربت الذي يفحص حالة
الخروج وحدها سيظن أنه نظّف.

.. code-block:: console

   $ rm ~/.local/share/libvirt/images/web-01.qcow2

الجهاز نفسه، بـ vmctl
---------------------

.. code-block:: yaml
   :caption: web-01.yaml

   name: web-01
   cpu:
     count: 2
   memory:
     mb: 128
   storage:
     - name: system
       size_mb: 1024
       bus: sata
       bootable: true
   networks:
     - network_type: nat
       model: virtio

.. code-block:: console

   $ vmctl -p libvirt import web-01.yaml --policy nearest --execute
   Warning: guest_os: ubuntu is not supported, used linux instead (libosinfo has no
   id for a family without a version)
   Created VM 'web-01' from web-01.yaml
       1: mkdir -p ~/.local/share/libvirt/images
       2: qemu-img create -f qcow2 ~/.local/share/libvirt/images/web-01_system.qcow2 1024M
       3: write /tmp/web-01.xml (942 bytes)
       4: virsh define /tmp/web-01.xml

وبدون ``--execute`` فهذه الأسطر الأربعة هي كل ما تحصل عليه، ولا شيء يُمَسّ. والتفكيك
يحذف الصورة أيضاً، لأن vmctl يعلم أنه أنشأها:

.. code-block:: console

   $ vmctl -p libvirt delete web-01 --force
   Deleted VM 'web-01'
   $ ls ~/.local/share/libvirt/images/web-01*
   ls: cannot access '/home/vagrant/.local/share/libvirt/images/web-01*': No such
   file or directory

جنباً إلى جنب
-------------

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * -
     - virsh
     - vmctl
   * - لوصف الجهاز
     - 38 سطر XML
     - 13 سطر YAML
   * - لبنائه
     - ``qemu-img create`` ثم ``virsh define``
     - أمر واحد
   * - مسار المُحاكي ونوع الآلة
     - تكتبهما، ولكل توزيعة قيمتها
     - يُحَلّان بحسب المضيف
   * - معاينة قبل التنفيذ
     - لا توجد؛ ``define`` يُعرِّف
     - التشغيل التجريبي هو الافتراضي
   * - إعداد غير مدعوم
     - يفشل تعريف النطاق
     - يُرفَض، أو يُستبدَل ويُبلَّغ عنه، بحسب ``--policy``
   * - الملف مقابل الجهاز الحقيقي
     - لا يُطابق ``dumpxml`` ملفك أبداً
     - يقارن ``vmctl diff`` ما يذكره الملف فقط
   * - تغيير حقل واحد
     - ``setmaxmem --config`` --- المحفوظ والعامل يختلفان بصمت
     - يُغيّر ``vmctl apply`` ما انحرف ويقول ما غيّره
   * - حذفه
     - يُترك القرص إلّا إن كان في مجمع تخزين
     - تُحذف الصور التي أنشأها vmctl
   * - مُشرف افتراضي آخر
     - أعد الكتابة لذلك المُشرف
     - الملف نفسه، مع ``-p`` مختلف

متى تستخدم virsh مباشرةً على أي حال
-----------------------------------

يُصدر vmctl أوامر ``virsh``؛ وهو ليس بديلاً عن معرفتها. الجأ إلى ``virsh`` مباشرةً
لكل ما لا يُمثّله vmctl:

* مجمعات التخزين وأحجامه --- ``pool-define`` و ``vol-create``
* شبكات libvirt --- ``net-define`` و ``net-start``
* الترحيل الحيّ --- ``migrate``
* التوصيل أثناء التشغيل --- ``attach-device`` و ``detach-device`` على نطاق عامل
* أي شيء في وثيقة XML لا يملك vmctl حقلاً له؛ ويسرد ``vmctl capabilities`` ما يمكنه
  التعبير عنه لهذا المُشرف، ويرفض ``--policy strict`` ما بقي بدلاً من التخمين

وعادة مفيدة أثناء تعلّم أيٍّ منهما: يكتب
``vmctl -p libvirt import vm.yaml --out ./`` وثيقة XML للنطاق وسكربت الصدفة الذي
*سيُنفّذه*، دون أن يُنفّذهما. وهي أسرع طريقة لترى كيف يصبح حقل كتبته وثيقةَ XML.
