# OpenPath Demo App

Minimal Flask web app bundled with OpenPath for self-testing.

## Run standalone

```bash
cd tests/fixtures/demo_app
pip install flask
python app.py
```

## What it has

| Route | Description |
|-------|-------------|
| `/` | Home page |
| `/login` | Login form (`demo` / `change-me` by default) |
| `/items` | Item list (CRUD) |
| `/items/new` | Create item |
| `/about` | Static about page |
| `/settings` | Theme toggle |
