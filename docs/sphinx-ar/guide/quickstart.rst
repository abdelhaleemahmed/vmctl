البدء السريع
============

أيّ مُشرف افتراضي؟
-------------------

كل الأوامر التالية تعمل بالطريقة نفسها على VirtualBox و libvirt/QEMU-KVM و QEMU
المجرّد و VMware Workstation. يختار vmctl واحداً — قيمة ``$VMCTL_PROVIDER`` إن
كانت مضبوطة، وإلا أول مُشرف مُثبَّت — والخيار ``-p`` يحدّد أيّها:

.. code-block:: bash

   vmctl providers                 # ما هو قابل للاستخدام هنا؛ النجمة تعني الافتراضي
   vmctl doctor                    # وهل هذه الآلة مهيّأة لتشغيل جهاز افتراضي أصلاً
   vmctl -p libvirt list
   export VMCTL_PROVIDER=libvirt   # أو اضبطه مرة واحدة

انظر :doc:`providers` لمعرفة ما يختلف بينها، ومجلد ``examples/`` فيه مثال مكتوب
بلهجة كل مُشرف افتراضي.

عرض قائمة الأجهزة الافتراضية
-----------------------------

.. code-block:: bash

   vmctl list
   vmctl list --format simple   # أسماء فقط
   vmctl list --format json     # للاستخدام في السكربتات

تصدير جهاز افتراضي
-------------------

احفظ إعدادات جهاز افتراضي نشط أو متوقف في ملف YAML:

.. code-block:: bash

   vmctl export ubuntu-server -o ubuntu-server.yaml
   vmctl export ubuntu-server -o ubuntu-server.json --format json

لا يلزم إيقاف الجهاز الافتراضي قبل التصدير.

استيراد جهاز افتراضي (إعادة الإنشاء من ملف)
--------------------------------------------

نفِّذ تشغيلاً تجريبياً أولاً لمراجعة ما سيتم إنشاؤه:

.. code-block:: bash

   vmctl import ubuntu-server.yaml --new-name test-server

ثم نفِّذ الأمر الفعلي:

.. code-block:: bash

   vmctl import ubuntu-server.yaml --new-name test-server --execute

.. note::

   يُنشئ الأمر ``import`` جهازاً افتراضياً جديداً بـ **قرص فارغ**؛ فملف الإعدادات
   يصف آلة لا محتوياتها. ولنسخ المحتويات أيضاً استخدم ``--clone-disks``، وذلك حين
   تكون صور الأقراص مقروءة من هذه الآلة — ويُسمّى الملفُّ صورتَه في الحقل
   ``source:``. وإلا فاستخدم Bareos أو rsync أو أداة مماثلة لاستعادة البيانات إلى
   الجهاز الافتراضي الجديد.

الاستنساخ من جهاز افتراضي موجود
---------------------------------

يقرأ الأمر ``create`` الإعدادات مباشرةً من المُشرف الافتراضي بدلاً من ملف:

.. code-block:: bash

   vmctl create ubuntu-server --new-name ubuntu-clone --execute
   vmctl create ubuntu-server --new-name small-clone --cpus 2 --memory 2048 --execute
   vmctl create ubuntu-server --new-name full-clone --clone-disks --execute

أوامر دورة حياة الجهاز الافتراضي
----------------------------------

.. code-block:: bash

   vmctl start ubuntu-server
   vmctl stop  ubuntu-server
   vmctl stop  ubuntu-server --force   # إيقاف قسري
   vmctl status ubuntu-server          # running / stopped / paused / saved
   vmctl delete old-vm                 # يطلب تأكيداً
   vmctl delete old-vm --force         # بدون تأكيد

إبقاء الجهاز الافتراضي مطابقاً لملفه
--------------------------------------

.. code-block:: bash

   vmctl diff  web-01 web-01.yaml      # قراءة فقط؛ يعيد 1 عند وجود اختلاف
   vmctl apply web-01.yaml             # تشغيل تجريبي: ما الذي سيتغيّر
   vmctl apply web-01.yaml --execute   # يغيّر ما اختلف فقط

لا يُطبَّق إلا ما يذكره الملف صراحةً، فالملف القصير يغيّر ما ذكره ويترك الباقي كما
هو عند المُشرف الافتراضي.

النسخ اللحظية (Snapshots)
--------------------------

.. code-block:: bash

   vmctl snapshot take web-01 before-upgrade -d "السبب"
   vmctl snapshot list web-01
   vmctl snapshot restore web-01 before-upgrade
   vmctl snapshot delete web-01 before-upgrade

التحقق من صحة ملف الإعدادات
-----------------------------

.. code-block:: bash

   vmctl validate my-config.yaml
   vmctl -p libvirt validate my-config.yaml   # فالصحة تتبع المُشرف الافتراضي
   vmctl validate my-config.yaml --format json

التحقق من أن كل شيء يعمل هنا
------------------------------

.. code-block:: bash

   vmctl doctor      # المضيف، والمُشرف الافتراضي، ومجلد الصور، والمساحة المتاحة
   vmctl selftest    # ينشئ جهازاً افتراضياً زائلاً ويتحقّق منه ويشغّله ويوقفه ويحذفه

الأمر ``doctor`` ينظر فقط، أما ``selftest`` فيستخدم المُشرف الافتراضي فعلاً — وبهذا
يُفرَّق بين إعداد يبدو صحيحاً ولا يشغّل جهازاً، وإعداد يعمل.
