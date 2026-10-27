# GitHub upload

This folder is sanitized for source control. Local secrets and runtime data are intentionally excluded by `.gitignore`.

## First upload

```powershell
cd "C:\path\to\message-ingestion-service-github"
git init
git add .
git status
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR-USER/YOUR-REPO.git
git push -u origin main
```

Before pushing, create a local `.env` from `.env.example` and keep real credentials only in `.env` (never commit them).

## Intentionally excluded

- `.env` and `.env.*` except `.env.example`
- PostgreSQL dumps / local backups
- logs
- Python virtual environments
- `node_modules`
- WhatsApp auth/session/state
- patch backup folders and local archives

## Production note

The repository keeps generic configuration examples. Your production machine can use a local Windows PostgreSQL service (for example port `5433`) by setting the real `DATABASE_URL` in the untracked `.env` file.
