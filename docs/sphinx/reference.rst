.. _generated-reference:

Generated reference
===================

Two tables that are generated from vmctl's own code rather than written by hand --
every configuration field, and what each hypervisor supports. Both drifted when they
were maintained by hand, which is why they no longer are: the field list comes from the
dataclasses that read a config file, and the support matrices from each provider's
capability declaration, which is the same one the validator and the translator read.

Regenerate with ``python scripts/generate-docs.py``; CI checks that the committed
copies are current.

Configuration fields
--------------------

.. include:: ../features.md
   :parser: myst_parser.sphinx_

What each hypervisor supports
-----------------------------

.. include:: ../providers.md
   :parser: myst_parser.sphinx_

Commands
--------

.. include:: ../commands.md
   :parser: myst_parser.sphinx_
