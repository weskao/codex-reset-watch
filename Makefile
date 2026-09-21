UV ?= uv
UV_PYTHON ?= 3.13
.PHONY: install uninstall sync lock check update check-no-tg monitor daily doctor logs config apply-schedule test test-unit test-integration lint reload launch-status tool-list tool-reinstall tool-uninstall

install:
	CRW_UV_PYTHON=$(UV_PYTHON) $(UV) run python scripts/install.py

uninstall:
	$(UV) run python scripts/uninstall.py

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

config:
	$(UV) run codex-reset-watch config

apply-schedule:
	$(UV) run codex-reset-watch apply-schedule

tool-list:
	$(UV) tool list

tool-reinstall:
	$(UV) tool install --force --python $(UV_PYTHON) .

tool-uninstall:
	$(UV) tool uninstall codex-reset-watch

launch-status:
	@launchctl print gui/$$(id -u)/com.wes.codex-reset-watch.daily 2>/dev/null | head -50 || true
	@launchctl print gui/$$(id -u)/com.wes.codex-reset-watch.monitor 2>/dev/null | head -50 || true

reload:
	CRW_UV_PYTHON=$(UV_PYTHON) $(UV) run python scripts/install.py

lint:
	$(UV) run python -m py_compile src/codex_reset_watch/__init__.py src/codex_reset_watch/paths.py \
		src/codex_reset_watch/filelock.py src/codex_reset_watch/config.py src/codex_reset_watch/scheduler.py \
		src/codex_reset_watch/ui.py src/codex_reset_watch/i18n.py src/codex_reset_watch/keys.py \
		src/codex_reset_watch/secrets_store.py src/codex_reset_watch/telegram_notify.py scripts/render_launchd.py scripts/render_systemd.py \
		scripts/schtasks.py scripts/install.py scripts/uninstall.py

test-unit:
	$(UV) run python -m unittest tests.test_parser tests.test_formatting tests.test_state tests.test_config \
		tests.test_config_extras tests.test_i18n tests.test_keys tests.test_secrets_store \
		tests.test_scheduler tests.test_ui tests.test_ui_menu tests.test_ui_mode tests.test_launchd -v

test-integration:
	$(UV) run python -m unittest tests.test_integration tests.test_run_check tests.test_cli_config \
		tests.test_cli_syntax tests.test_install tests.test_install_telegram -v

test: lint
	$(UV) run python -m unittest discover -s tests -t . -v
