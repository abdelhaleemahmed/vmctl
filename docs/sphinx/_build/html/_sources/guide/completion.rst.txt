Shell Tab Completion
====================

vmctl supports tab completion for all commands and VM names in **bash**,
**zsh**, and **fish**.

Bash
----

Activate for the current session:

.. code-block:: bash

   eval "$(_VMCTL_COMPLETE=bash_source vmctl)"

Make it permanent — add to ``~/.bashrc``:

.. code-block:: bash

   echo 'eval "$(_VMCTL_COMPLETE=bash_source vmctl)"' >> ~/.bashrc
   source ~/.bashrc

Zsh
---

Activate for the current session:

.. code-block:: zsh

   eval "$(_VMCTL_COMPLETE=zsh_source vmctl)"

Make it permanent — add to ``~/.zshrc``:

.. code-block:: zsh

   echo 'eval "$(_VMCTL_COMPLETE=zsh_source vmctl)"' >> ~/.zshrc
   source ~/.zshrc

Fish
----

Activate for the current session:

.. code-block:: fish

   _VMCTL_COMPLETE=fish_source vmctl | source

Make it permanent — add to ``~/.config/fish/config.fish``:

.. code-block:: fish

   echo '_VMCTL_COMPLETE=fish_source vmctl | source' >> ~/.config/fish/config.fish

What gets completed
-------------------

* All **command names** (``list``, ``start``, ``export``, …)
* All **VM names** — vmctl queries VirtualBox live and returns only VM
  names that match the characters already typed
* **Option names** (``--format``, ``--execute``, ``--force``, …)
* **Choice values** — e.g. ``--format <TAB>`` shows ``yaml`` and ``json``
