# vmctl documentation build.
#
#   make html    -> build/html/   (English HTML)
#   make clean   -> remove the build directory

SPHINXBUILD ?= python -m sphinx
SOURCEDIR    = source
BUILDDIR     = build

.PHONY: help clean html linkcheck

help:
	@echo "html       Build HTML  -> $(BUILDDIR)/html"
	@echo "clean      Remove the build directory"
	@echo "linkcheck  Check external links"

clean:
	rm -rf $(BUILDDIR)

html:
	$(SPHINXBUILD) -b html $(SOURCEDIR) $(BUILDDIR)/html

linkcheck:
	$(SPHINXBUILD) -b linkcheck $(SOURCEDIR) $(BUILDDIR)/linkcheck
