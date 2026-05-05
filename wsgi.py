"""Point d'entree WSGI pour gunicorn / waitress.

Usage : `gunicorn -w 2 -b 127.0.0.1:5034 wsgi:app`
"""
from app import app  # noqa: F401
