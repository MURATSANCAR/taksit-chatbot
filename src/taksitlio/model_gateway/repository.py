"""In-memory / DB-backed profile repositories for ModelGateway."""

from __future__ import annotations

from typing import Iterable

from taksitlio.model_gateway.gateway import ModelProfile, ProfileRepository


class InMemoryProfileRepository(ProfileRepository):
    def __init__(self, profiles: Iterable[ModelProfile]) -> None:
        self._by_code = {p.profile_code: p for p in profiles}
        self._by_id = {p.id: p for p in self._by_code.values()}

    def get_by_code(self, profile_code: str) -> ModelProfile:
        try:
            return self._by_code[profile_code]
        except KeyError as exc:
            raise KeyError(f"Unknown model profile_code: {profile_code}") from exc

    def get_by_id(self, profile_id: int) -> ModelProfile:
        try:
            return self._by_id[profile_id]
        except KeyError as exc:
            raise KeyError(f"Unknown model profile id: {profile_id}") from exc
