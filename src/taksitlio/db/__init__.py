"""Database package."""

from taksitlio.db.pool import create_pool

__all__ = ["create_pool"]


def __getattr__(name: str):
    if name == "migrate":
        from taksitlio.db import migrate as migrate_mod

        return migrate_mod
    raise AttributeError(name)
