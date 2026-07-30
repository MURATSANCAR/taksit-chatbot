"""Filesystem-backed dataset repository.

The repository exposes datasets by split so the runner and CLI never
have to know the on-disk layout. It also caches parsed datasets by
absolute path — datasets are treated as immutable once loaded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from taksitlio.evaluation.dataset import load_jsonl, split_from_path
from taksitlio.evaluation.domain import DatasetSplit, EvaluationDataset


class FilesystemDatasetRepository:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._cache: dict[str, EvaluationDataset] = {}

    def list_datasets(self, split: DatasetSplit) -> list[Path]:
        # Search every subdirectory: validation + holdout share the
        # `golden/` folder in the project layout, so a strict per-split
        # directory lookup would miss them.
        if not self._root.exists():
            return []
        matches: list[Path] = []
        for path in self._root.rglob("*.jsonl"):
            if not path.is_file():
                continue
            try:
                inferred = split_from_path(path)
            except Exception:  # noqa: BLE001 — ignore unclassifiable files
                continue
            if inferred == split:
                matches.append(path)
        return sorted(matches)

    def load(self, path: Path) -> EvaluationDataset:
        resolved = str(Path(path).resolve())
        cached = self._cache.get(resolved)
        if cached is not None:
            return cached
        dataset = load_jsonl(Path(path))
        self._cache[resolved] = dataset
        return dataset

    def load_all(self, split: DatasetSplit) -> list[EvaluationDataset]:
        return [self.load(p) for p in self.list_datasets(split)]


__all__ = ["FilesystemDatasetRepository"]
