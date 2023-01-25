VERSION ?= 0.0.0.dev0
V = 0
Q = $(if $(filter 1,$V),,@)

M = $(shell if [ "$$(tput colors 2> /dev/null || echo 0)" -ge 8 ]; then printf "\033[34;1m▶\033[0m"; else printf "▶"; fi)

export VERSION

.SUFFIXES:
.PHONY: all
all: | tests wheels  ## Runs the tests and builds the wheel

.PHONY: format
format: ## Runs black to format the python code
	$(Q) hatch run tests:format

# Standard targets

.PHONY: wheels
wheels:  ## Build python wheels
	$(Q) rm -Rf build
	$(Q) hatch build -t wheel

.PHONY: tests
tests:; $(info $(M) Running tests...) @ ## Run tests with coverage
	$(Q) hatch run tests:all

.PHONY: clean
clean: ## Cleanup everything
	$(info $(M) cleaning ...)
	$(Q) rm -Rf dist application_kit.egg-info coverage.xml

.PHONY: env
env: ## Builds development virtualenv
	$(Q) hatch env remove tests
	$(Q) hatch env create tests

.PHONY: binary
binary: ## Builds a standalone binary using pyoxydizer
	$(Q) hatch run binary:oxydizer

.PHONY: binary-nuitka
binary-nuitka: ## Builds a standalone binary using nuitka
	$(Q) hatch run binary:build

.PHONY: release
release: wheels binary ## Builds a windows binary and creates a release
	$(Q) hatch publish
	$(Q) zip -j a816 build/x86_64-pc-windows-msvc/release/install/*
	$(Q) gh release create $(VERSION) --generate-notes a816.zip

.PHONY: help
help: ## Display help
	@grep -hE '^[ a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-17s\033[0m %s\n", $$1, $$2}'
