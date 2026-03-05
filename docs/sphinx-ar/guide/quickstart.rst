البدء السريع
============

عرض قائمة الأجهزة الافتراضية
-----------------------------

.. code-block:: bash

   vmctl list
   vmctl list --format simple   # أسماء فقط

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

   يُنشئ الأمر ``import`` جهازاً افتراضياً جديداً بـ **قرص فارغ**، ولا ينسخ
   محتويات القرص. استخدم Bareos أو rsync أو أداة مماثلة لاستعادة البيانات إلى
   الجهاز الافتراضي الجديد.

الاستنساخ من جهاز افتراضي موجود
---------------------------------

يقرأ الأمر ``create`` الإعدادات مباشرةً من VirtualBox بدلاً من ملف:

.. code-block:: bash

   vmctl create ubuntu-server --new-name ubuntu-clone --execute
   vmctl create ubuntu-server --new-name small-clone --cpus 2 --memory 2048 --execute

أوامر دورة حياة الجهاز الافتراضي
----------------------------------

.. code-block:: bash

   vmctl start ubuntu-server
   vmctl stop  ubuntu-server
   vmctl stop  ubuntu-server --force   # إيقاف قسري
   vmctl status ubuntu-server          # running / stopped / paused / saved
   vmctl delete old-vm                 # يطلب تأكيداً
   vmctl delete old-vm --force         # بدون تأكيد

التحقق من صحة ملف الإعدادات
-----------------------------

.. code-block:: bash

   vmctl validate my-config.yaml
