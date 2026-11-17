.PHONY: setup dev test reset-demo
setup:
	python -m venv .venv
	.venv/bin/pip install -r backend/requirements.txt
	cd frontend && npm install
dev:
	@trap 'kill 0' INT TERM EXIT; cd backend && ../.venv/bin/uvicorn app.main:app --port 8000 & cd frontend && npm run dev & wait
test:
	cd backend && ../.venv/bin/python -m pytest
reset-demo:
	@echo "Disponível na etapa 3."
