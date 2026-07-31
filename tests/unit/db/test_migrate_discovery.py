"""Migrate helper discovers ordered V*.sql files (no DB required)."""

from pathlib import Path

from taksitlio.db.migrate import migration_files


def test_migration_files_are_version_ordered() -> None:
    files = migration_files()
    assert files, "expected db/migrations/V*.sql"
    names = [p.name for p in files]
    assert names == sorted(names)
    assert names[0].startswith("V001")
    assert any(n.startswith("V014") for n in names)
    assert any(n.startswith("V015") for n in names)
    assert any(n.startswith("V016") for n in names)
    assert any(n.startswith("V017") for n in names)
    assert any(n.startswith("V018") for n in names)
    assert any(n.startswith("V019") for n in names)
    assert any(n.startswith("V020") for n in names)
    assert any(n.startswith("V021") for n in names)
    assert all(isinstance(p, Path) and p.suffix == ".sql" for p in files)
