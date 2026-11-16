# Python frameworks

Part of [Deploying by framework](frameworks.md).

Python projects are detected by `requirements.txt`, `pyproject.toml`, `setup.py` or `Pipfile`.
Noust builds a virtual environment (Poetry or Pipenv when the project is locked with them) and runs
Gunicorn on `127.0.0.1`, with Uvicorn workers for FastAPI and Starlette (Hypercorn when the project
depends on it). Pin the interpreter with `runtime: python@3.12`.

## Django

```bash
noust create -d site.example.com -s https://github.com/you/site.git
```

The project's own `wsgi.py` or `asgi.py` is found; `collectstatic` runs when the settings define a
`STATIC_ROOT` (and Noust serves it); put the migration in a hook:

```yaml
hooks:
  pre_deploy:
    - run: python manage.py migrate --noinput
      migrates: true
```

Add the domain to `ALLOWED_HOSTS` (Noust does not guess it), and keep `SECRET_KEY` and the
database URL in `.env`.

## Flask and FastAPI

Noust's default start is `gunicorn app:app -b 127.0.0.1:PORT`, with Uvicorn workers for an ASGI
application (FastAPI, Starlette). If the application object is not `app` in `app.py`, say what to
run with `start:` in the `build:` block (`PORT` is in the environment):

```yaml
build:
  start: gunicorn -b 127.0.0.1:${PORT} -k uvicorn.workers.UvicornWorker app.main:app
```
