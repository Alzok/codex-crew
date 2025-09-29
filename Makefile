PYTHON ?= python3

run:
	$(PYTHON) -m mcp.cli.app run "echo hello"

lint:
	$(PYTHON) -m compileall src
.PHONY: run lint
