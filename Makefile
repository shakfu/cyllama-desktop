# cyllama-desktop build orchestration.
#
# Default target builds the macOS arm64 .dmg end-to-end:
#   1. npm install        (if node_modules is missing)
#   2. build the bundled Python env via python-build-standalone
#   3. electron-builder package
#
# Override the target with PLATFORM/ARCH, or invoke specific phony targets.

SHELL := /usr/bin/env bash

# Detect host triple for the Python env directory naming.
UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)

ifeq ($(UNAME_S),Darwin)
  HOST_OS  := mac
else ifeq ($(UNAME_S),Linux)
  HOST_OS  := linux
else
  HOST_OS  := win
endif

ifeq ($(UNAME_M),arm64)
  HOST_ARCH := arm64
else ifeq ($(UNAME_M),aarch64)
  HOST_ARCH := arm64
else
  HOST_ARCH := x64
endif

PYENV_DIR := build/python-$(HOST_OS)-$(HOST_ARCH)
PY_BIN    := $(PYENV_DIR)/bin/python3

.PHONY: all dev dmg python python-local npm test test-deps e2e clean reset help \
        variant variant-cpu variant-cuda variant-vulkan variant-rocm variant-sycl \
        app-cpu app-cuda app-vulkan app-rocm app-sycl

# Default path to a local cyllama checkout. Override at invocation
# (``make python-local CYLLAMA_SOURCE=/elsewhere/cyllama``) or via the
# environment.
CYLLAMA_SOURCE ?= $(abspath ../cyllama)

all: dmg

help:
	@echo "Targets:"
	@echo "  make           Build a distributable .dmg for the host arch (default)"
	@echo "  make dev       Run the app in dev mode (npm start)"
	@echo "  make dmg       Build the installer for the host (mac=dmg, win=nsis, linux=AppImage)"
	@echo "  make python    Build the bundled Python env only (cyllama from PyPI)"
	@echo "  make python-local"
	@echo "                 Rebuild the bundled Python env using a local cyllama"
	@echo "                 checkout (default: ../cyllama; override CYLLAMA_SOURCE)"
	@echo ""
	@echo "GPU variants (bundle a per-backend cyllama distribution):"
	@echo "  make variant   Show which variant the build is currently pinned to"
	@echo "  make variant-cpu / -cuda / -vulkan / -rocm / -sycl"
	@echo "                 Switch variant and rebuild the bundled Python env"
	@echo "  make app-cpu / -cuda / -vulkan / -rocm / -sycl"
	@echo "                 Switch variant and build the installer for it"
	@echo "                 (macOS arm64: 'cpu' already includes Metal)"
	@echo "  make npm       npm install"
	@echo "  make test      Run the sidecar pytest suite"
	@echo "  make e2e       Run the Playwright per-pane smoke suite"
	@echo "  make clean     Remove dist/ and build/"
	@echo "  make reset     clean + remove node_modules/"

# --- phases -----------------------------------------------------------------

node_modules: package.json
	npm install
	@touch node_modules

npm: node_modules

$(PY_BIN):
	bash scripts/build-python-env.sh

python: $(PY_BIN)

# Rebuild the bundled Python env using a local cyllama checkout. Always
# wipes the existing env first so the rebuild actually runs (the plain
# ``python`` target is gated on $(PY_BIN) existing). The build-python-env.sh
# script honors CYLLAMA_SOURCE; we just guarantee the rebuild fires.
python-local:
	@if [ ! -d "$(CYLLAMA_SOURCE)" ]; then \
	  echo "CYLLAMA_SOURCE=$(CYLLAMA_SOURCE) does not exist"; exit 1; \
	fi
	@echo "Rebuilding bundled Python env from $(CYLLAMA_SOURCE)"
	rm -rf "$(PYENV_DIR)"
	CYLLAMA_SOURCE="$(CYLLAMA_SOURCE)" bash scripts/build-python-env.sh

# --- GPU variants -----------------------------------------------------------
#
# cyllama ships one distribution per backend, all installing as
# ``import cyllama``. scripts/set-cyllama-variant.py records the choice in
# python-sidecar/pyproject.toml (which build-python-env.sh reads back) and
# renames the installer so variants don't overwrite each other in dist/.
#
# The env is wiped rather than upgraded in place: two cyllama distributions
# own the same site-packages/cyllama directory, so switching backends means
# a clean install, not a pip upgrade.

VARIANT_SCRIPT := scripts/set-cyllama-variant.py

# Written out one target per backend rather than as a ``variant-%``
# pattern rule: GNU make excludes .PHONY targets from implicit-rule
# search, so a pattern rule here matches nothing and every target reports
# "Nothing to be done". The recipes are identical apart from the backend
# name, so they share these two canned recipes.
define switch_variant
	@python3 $(VARIANT_SCRIPT) $(1)
	rm -rf "$(PYENV_DIR)"
	bash scripts/build-python-env.sh
endef

ifeq ($(HOST_OS),mac)
  BUILD_APP := npm run build:mac-$(HOST_ARCH)
else ifeq ($(HOST_OS),win)
  BUILD_APP := npm run build:win
else
  BUILD_APP := npm run build:linux
endif

variant:
	@python3 $(VARIANT_SCRIPT) --show

variant-cpu:
	$(call switch_variant,cpu)

variant-cuda:
	$(call switch_variant,cuda)

variant-vulkan:
	$(call switch_variant,vulkan)

variant-rocm:
	$(call switch_variant,rocm)

variant-sycl:
	$(call switch_variant,sycl)

app-cpu: variant-cpu node_modules
	$(BUILD_APP)

app-cuda: variant-cuda node_modules
	$(BUILD_APP)

app-vulkan: variant-vulkan node_modules
	$(BUILD_APP)

app-rocm: variant-rocm node_modules
	$(BUILD_APP)

app-sycl: variant-sycl node_modules
	$(BUILD_APP)

dev: node_modules python
	npm start

dmg: node_modules python
ifeq ($(HOST_OS),mac)
	npm run build:mac-$(HOST_ARCH)
else ifeq ($(HOST_OS),win)
	npm run build:win
else
	npm run build:linux
endif

# --- tests ------------------------------------------------------------------
#
# Tests stub ``cyllama`` (see tests/conftest.py), so they only need pytest +
# fastapi + httpx. We install these into the bundled Python env if it exists
# (avoids polluting the user's system Python), otherwise fall back to whatever
# ``python3`` is on PATH. Pytest is *not* shipped in the dmg -- it's reinstalled
# into the bundled env on demand and pruned by build-python-env.sh on rebuild.

PYTEST_PY := $(shell test -x "$(PY_BIN)" && echo "$(PY_BIN)" || command -v python3)

test-deps:
	@if [ -z "$(PYTEST_PY)" ]; then \
	  echo "no python3 found"; exit 1; \
	fi
	@$(PYTEST_PY) -c "import pytest, fastapi, httpx, multipart" 2>/dev/null || \
	  $(PYTEST_PY) -m pip install --quiet pytest "fastapi>=0.115" "httpx>=0.27" \
	    "python-multipart>=0.0.9"

test: test-deps
	$(PYTEST_PY) -m pytest tests/ --ignore=tests/e2e -v

# Playwright per-pane smoke. Boots Electron against a stubbed sidecar
# (tests/e2e/sidecar_launcher.py installs the same conftest cyllama
# stub the pytest suite uses) so it doesn't need a real cyllama backend
# or any GGUF files. node_modules covers @playwright/test; the
# bundled Python env covers fastapi + uvicorn already.
e2e: node_modules test-deps
	npm run test:e2e

clean:
	rm -rf dist build

reset: clean
	rm -rf node_modules
