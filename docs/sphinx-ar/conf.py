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
release = '1.1.9'
version = '1.1.9'
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

source_suffix = {
    '.rst': None,
    '.md': 'myst',
}

master_doc = 'index'
suppress_warnings = ['autodoc']
