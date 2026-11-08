# buildsen_api

API BUILDSEN (FastAPI, PostgreSQL, Alembic) : plateforme de mise en relation BTP au Sénégal.

- Configuration : copier `.env.example` en `.env` (ne jamais le publier).
- Image Docker : `docker build -t buildsen-api .` (utilisateur non root, vérification de santé sur `/api/health`).
- Tests : `pip install -r requirements-dev.txt && python -m pytest`.
- Premier administrateur : `python -m app.createadmin email prénom nom`.
- Déploiement complet (AWS EC2, Docker, HTTPS) : voir le dépôt du site, fichier `DEPLOIEMENT-EC2.md`.
