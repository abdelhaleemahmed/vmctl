الإكمال التلقائي في الطرفية
============================

يدعم vmctl الإكمال التلقائي لجميع الأوامر وأسماء الأجهزة الافتراضية في
**bash** و**zsh** و**fish**.

Bash
----

تفعيل الإكمال للجلسة الحالية:

.. code-block:: bash

   eval "$(_VMCTL_COMPLETE=bash_source vmctl)"

لجعله دائماً — أضف هذا السطر إلى ``~/.bashrc``:

.. code-block:: bash

   echo 'eval "$(_VMCTL_COMPLETE=bash_source vmctl)"' >> ~/.bashrc
   source ~/.bashrc

Zsh
---

تفعيل الإكمال للجلسة الحالية:

.. code-block:: zsh

   eval "$(_VMCTL_COMPLETE=zsh_source vmctl)"

لجعله دائماً — أضف هذا السطر إلى ``~/.zshrc``:

.. code-block:: zsh

   echo 'eval "$(_VMCTL_COMPLETE=zsh_source vmctl)"' >> ~/.zshrc
   source ~/.zshrc

Fish
----

تفعيل الإكمال للجلسة الحالية:

.. code-block:: fish

   _VMCTL_COMPLETE=fish_source vmctl | source

لجعله دائماً — أضف هذا السطر إلى ``~/.config/fish/config.fish``:

.. code-block:: fish

   echo '_VMCTL_COMPLETE=fish_source vmctl | source' >> ~/.config/fish/config.fish

ما يُكمَل تلقائياً
-------------------

* **أسماء الأوامر**: ``list``، ``start``، ``export``، ...
* **أسماء الأجهزة الافتراضية** — يستعلم vmctl من VirtualBox مباشرةً ويعرض فقط الأسماء المطابقة لما كُتب
* **أسماء الخيارات**: ``--format``، ``--execute``، ``--force``، ...
* **قيم الاختيارات** — مثلاً: ``--format <TAB>`` يعرض ``yaml`` و``json``
