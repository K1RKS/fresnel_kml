# fresnel_kml. `make` lists the targets. PYTHON, HOST and PORT can be overridden:
#   make test PYTHON=python3
#   make serve PORT=8080

ifeq ($(origin PYTHON), undefined)
  ifneq ($(wildcard .venv/bin/python),)
    PYTHON := .venv/bin/python
  else
    PYTHON := python3
  endif
endif

HOST ?= 127.0.0.1
PORT ?= 8000

.PHONY: help test site pyodide serve clean

.DEFAULT_GOAL := help

help:
	@echo "test      run pytest"
	@echo "site      build dist/site/ and dist/fresnel-site.tar.gz (downloads Pyodide on first run)"
	@echo "pyodide   download the pinned Pyodide runtime into web/pyodide/"
	@echo "serve     fetch Pyodide if needed, then serve the page at http://$(HOST):$(PORT)/"
	@echo "clean     remove dist/"

test:
	$(PYTHON) -m pytest

site:
	$(PYTHON) tools/build_site.py

pyodide:
	$(PYTHON) tools/fetch_pyodide.py

serve: pyodide
	$(PYTHON) tools/dev_server.py --host $(HOST) --port $(PORT)

clean:
	rm -rf dist
