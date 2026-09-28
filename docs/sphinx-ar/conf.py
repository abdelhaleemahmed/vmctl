# Configuration file for the Arabic Sphinx documentation builder.

import os
import sys

sys.path.insert(0, os.path.abspath('../..'))

# ---------------------------------------------------------------------------
# Project information
# ---------------------------------------------------------------------------

project = 'vmctl'
copyright = '2025، أحمد عبدالحليم أحمد'
author = 'أحمد عبدالحليم أحمد'
# Single-sourced from vmctl/__init__.py, like the package itself (H-03): two
# documentation trees with their own copies of the version number is two more
# places to forget, and both had already fallen behind.
from vmctl import __version__ as release  # noqa: E402

version = release
language = 'ar'

# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinx_click',
    'myst_parser',
]

autodoc_default_options = {
    'members': True,
    'member-order': 'bysource',
    'special-members': '__init__, __post_init__',
    'show-inheritance': True,
}

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
# انظر conf.py الإنجليزي: يمنع وصف الحقل مرتين (مرة من docstring ومرة من autodoc).
napoleon_use_ivar = True

intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
}

# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------

html_theme = 'sphinx_rtd_theme'
html_theme_options = {
    'navigation_depth': 4,
    'collapse_navigation': False,
    'sticky_navigation': True,
}

html_static_path = ['_static']
html_title = f'vmctl {release} — التوثيق العربي'
html_css_files = ['rtl.css']
html_js_files = ['fix-rtl.js']

source_suffix = {
    '.rst': None,
    '.md': 'myst',
}

master_doc = 'index'
suppress_warnings = ['autodoc']
