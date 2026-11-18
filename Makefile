.PHONY: setup dev test reset-demo
setup:
	python -m venv .venv
	.venv/bin/pip install -r backend/requirements.txt
	cd frontend && npm install
dev:
	bash backend/scripts/dev-linux.sh
test:
	cd backend && ../.venv/bin/python -m pytest
reset-demo:
	@echo "Disponível na etapa 3."
