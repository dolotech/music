SHELL := /bin/bash
.DEFAULT_GOAL := help

APP_NAME := SoundCloudMusic
VENV ?= .venv
PYTHON ?= $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)

.PHONY: help
help: ## 显示可用命令
	@echo "SoundCloud Music 统一命令："
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "  首次开发：make deps"
	@echo "  启动界面：make run"
	@echo "  提交之前：make check"
	@echo "  构建验证包：make package-dev"

.PHONY: venv
venv: ## 创建开发虚拟环境
	@if [ ! -x "$(VENV)/bin/python" ]; then python3 -m venv "$(VENV)"; fi

.PHONY: deps
deps: venv ## 安装开发与构建依赖
	@$(VENV)/bin/python -m pip install -r requirements-dev.txt

.PHONY: run
run: ## 启动图形界面
	@./music_gui.sh

.PHONY: cli
cli: ## 运行命令行，例如 make cli CLI_ARGS="--help"
	@./music.sh --help $(CLI_ARGS)

.PHONY: test
test: ## 运行全部离线测试
	@SDL_AUDIODRIVER=dummy $(PYTHON) -m pytest -m "not network"

.PHONY: lint
lint: ## Ruff 静态检查
	@$(PYTHON) -m ruff check music_app tests tools soundcloud_gui.py soundcloud_downloader.py

.PHONY: typecheck
typecheck: ## Mypy 类型检查
	@$(PYTHON) -m mypy music_app --ignore-missing-imports

.PHONY: selftest
selftest: ## 运行无网络自检
	@$(PYTHON) -m music_app.selftest

.PHONY: check
check: lint typecheck test ## 统一质量门禁

.PHONY: package-dev
package-dev: check ## 构建未签名开发验证包
	@$(PYTHON) tools/build_pyinstaller.py SoundCloudMusic.spec --noconfirm --clean

.PHONY: package
package: check ## 构建正式包（签名身份通过 CODESIGN_IDENTITY 传入）
	@$(PYTHON) tools/build_pyinstaller.py SoundCloudMusic.spec --noconfirm --clean --release

.PHONY: clean
clean: ## 清理 PyInstaller 构建产物
	@rm -rf build dist .pytest_cache .mypy_cache .ruff_cache .coverage
	@find . -path './.venv' -prune -o -name '__pycache__' -type d -exec rm -rf {} +
