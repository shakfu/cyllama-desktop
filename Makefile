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

.PHONY: all dev dmg python npm clean reset help

all: dmg

help:
	@echo "Targets:"
	@echo "  make           Build a distributable .dmg for the host arch (default)"
	@echo "  make dev       Run the app in dev mode (npm start)"
	@echo "  make dmg       Build the installer for the host (mac=dmg, win=nsis, linux=AppImage)"
	@echo "  make python    Build the bundled Python env only"
	@echo "  make npm       npm install"
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

clean:
	rm -rf dist build

reset: clean
	rm -rf node_modules
