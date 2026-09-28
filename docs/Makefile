# vmctl documentation build.
#
#   make html    -> build/html/      (English)
#   make ar      -> build/html/ar/    (Arabic)
#   make clean   -> remove the build directory
#
# Two trees, because the guides are translated prose rather than generated text.
# Both build with -W: a warning in the docs is a broken doc.

SPHINXBUILD ?= python -m sphinx
SOURCEDIR    = sphinx
ARSOURCEDIR  = sphinx-ar
BUILDDIR     = build

.PHONY: help clean html ar all linkcheck

help:
	@echo "html       Build the English HTML  -> $(BUILDDIR)/html"
	@echo "ar         Build the Arabic HTML   -> $(BUILDDIR)/html/ar"
	@echo "all        Both"
	@echo "clean      Remove the build directory"
	@echo "linkcheck  Check external links"

clean:
	rm -rf $(BUILDDIR)

html:
	$(SPHINXBUILD) -b html -W $(SOURCEDIR) $(BUILDDIR)/html

ar:
	$(SPHINXBUILD) -b html -W $(ARSOURCEDIR) $(BUILDDIR)/html/ar

all: html ar

linkcheck:
	$(SPHINXBUILD) -b linkcheck $(SOURCEDIR) $(BUILDDIR)/linkcheck
