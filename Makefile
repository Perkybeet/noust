.PHONY: help install dev-install build clean test lint format debian obs

help:
	@echo "Noust Makefile"
	@echo ""
	@echo "Usage:"
	@echo "  make install           Install Noust system-wide"
	@echo "  make dev-install       Install Noust in development mode"
	@echo "  make build             Build the package"
	@echo "  make clean             Remove build artifacts"
	@echo "  make test              Run tests"
	@echo "  make lint              Run linters"
	@echo "  make format            Format code"
	@echo ""
	@echo "Packaging (the release workflow uploads to OBS; these only look):"
	@echo "  make obs-status        Check OBS build status"
	@echo "  make obs-logs          View OBS build logs"
	@echo ""

install:
	pip install .

dev-install:
	pip install -e ".[dev]"

build:
	python -m build

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf src/*.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .pybuild/
	rm -f *.deb
	rm -f *.changes
	rm -f *.buildinfo
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

test:
	pytest -n auto

test-cov:
	pytest -n auto --cov=noust --cov-report=html
	@echo "Coverage report: htmlcov/index.html"

lint:
	ruff check src/noust tests
	mypy src/noust

format:
	black src/noust tests
	isort src/noust tests
	ruff check --fix src/noust tests

# OBS (Open Build Service) targets
obs-status:
	@echo "Checking OBS build status..."
	@if [ ! -f ~/.oscrc ]; then \
		echo "Error: OSC not configured"; \
		exit 1; \
	fi
	osc results home:Perkybeet noust

obs-status-watch:
	@echo "Watching OBS build status (Ctrl+C to stop)..."
	@if [ ! -f ~/.oscrc ]; then \
		echo "Error: OSC not configured"; \
		exit 1; \
	fi
	watch -n 10 "osc results home:Perkybeet noust"

obs-logs:
	@echo "Fetching OBS build logs..."
	@echo "Usage: make obs-logs DISTRO=Fedora_40 ARCH=x86_64"
	@if [ -z "$(DISTRO)" ] || [ -z "$(ARCH)" ]; then \
		echo "Error: DISTRO and ARCH required"; \
		echo "Example: make obs-logs DISTRO=Fedora_40 ARCH=x86_64"; \
		exit 1; \
	fi
	osc buildlog home:Perkybeet noust $(DISTRO) $(ARCH)
