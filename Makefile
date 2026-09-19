UV ?= uv
UV_PYTHON ?= 3.13
.PHONY: install uninstall sync lock check update check-no-tg monitor daily doctor logs test test-unit test-integration lint reload launch-status tool-list tool-reinstall tool-uninstall

install:
	CRW_UV_PYTHON=$(UV_PYTHON) ./scripts/install.sh

uninstall:
	./scripts/uninstall.sh

sync:
	$(UV) sync --python $(UV_PYTHON)

lock:
	$(UV) lock

check:
	$(UV) run codex-reset-watch check

update:
	$(UV) run codex-reset-watch update

check-no-tg:
	$(UV) run codex-reset-watch check --no-notify

monitor:
	$(UV) run codex-reset-watch monitor

daily:
	$(UV) run codex-reset-watch daily --force

doctor:
	$(UV) run codex-reset-watch doctor

logs:
	$(UV) run codex-reset-watch logs -n 50

tool-list:
	$(UV) tool list

tool-reinstall:
	UV_TOOL_BIN_DIR="$$HOME/scripts" $(UV) tool install --force --python $(UV_PYTHON) .

tool-uninstall:
	UV_TOOL_BIN_DIR="$$HOME/scripts" $(UV) tool uninstall codex-reset-watch

launch-status:
	@launchctl print gui/$$(id -u)/com.wes.codex-reset-watch.daily 2>/dev/null | head -50 || true
	@launchctl print gui/$$(id -u)/com.wes.codex-reset-watch.monitor 2>/dev/null | head -50 || true

reload:
	CRW_UV_PYTHON=$(UV_PYTHON) ./scripts/install.sh

lint:
	$(UV) run python -m py_compile src/codex_reset_watch/__init__.py scripts/render_launchd.py
	bash -n scripts/install.sh scripts/uninstall.sh

test-unit:
	$(UV) run python -m unittest tests.test_parser tests.test_formatting tests.test_state tests.test_launchd -v

test-integration:
	$(UV) run python -m unittest tests.test_integration -v

test: lint
	$(UV) run python -m unittest discover -s tests -v
