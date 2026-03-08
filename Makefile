checkpoints:
	modal run modal_app.py

deploy-backend:
	modal deploy modal_app.py

deploy-frontend:
	npm run build

deploy: deploy-backend deploy-frontend

dev-backend:
	uvicorn app.main:app --reload

dev-frontend:
	npm run dev
