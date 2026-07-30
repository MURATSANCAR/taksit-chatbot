"""CLI entrypoint: uvicorn taksitlio.main:app"""

from taksitlio.api.app import create_app

app = create_app()
