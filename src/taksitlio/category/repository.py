"""Postgres category catalog repository."""

from __future__ import annotations

from typing import Any

import asyncpg

from taksitlio.category.matcher import Category, CategoryMatchPolicy


class PostgresCategoryRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_active(self) -> list[Category]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    c.id, c.category_code, c.display_name, c.description,
                    c.synonyms, c.status, c.metadata,
                    ce.embedding
                FROM categories c
                LEFT JOIN LATERAL (
                    SELECT embedding
                    FROM category_embeddings ce
                    WHERE ce.category_id = c.id
                    ORDER BY ce.updated_at DESC
                    LIMIT 1
                ) ce ON TRUE
                WHERE c.status = 'ACTIVE'
                ORDER BY c.id
                """
            )
        result: list[Category] = []
        for row in rows:
            emb = row["embedding"]
            embedding = tuple(float(x) for x in emb) if emb else None
            synonyms = tuple(row["synonyms"] or ())
            metadata = row["metadata"] or {}
            if not isinstance(metadata, dict):
                metadata = dict(metadata)
            result.append(
                Category(
                    id=int(row["id"]),
                    category_code=row["category_code"],
                    display_name=row["display_name"],
                    description=row["description"],
                    synonyms=synonyms,
                    status=row["status"],
                    embedding=embedding,
                    metadata=metadata,
                )
            )
        return result

    async def get_match_policy(self, policy_code: str = "DEFAULT") -> CategoryMatchPolicy:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM category_match_policies WHERE policy_code = $1",
                policy_code,
            )
        if row is None:
            return CategoryMatchPolicy(policy_code=policy_code)
        return CategoryMatchPolicy(
            policy_code=row["policy_code"],
            minimum_score=float(row["minimum_score"]),
            maximum_candidates=int(row["maximum_candidates"]),
            clarify_score_gap=float(row["clarify_score_gap"]),
        )

    async def upsert_embedding(
        self,
        category_id: int,
        model_profile_id: int,
        embedding: list[float],
        source_text: str,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO category_embeddings (
                    category_id, model_profile_id, embedding, embedding_dim, source_text
                ) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (category_id, model_profile_id) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    embedding_dim = EXCLUDED.embedding_dim,
                    source_text = EXCLUDED.source_text,
                    updated_at = NOW()
                """,
                category_id,
                model_profile_id,
                embedding,
                len(embedding),
                source_text,
            )
