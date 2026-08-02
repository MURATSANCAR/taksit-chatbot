"""Regression: jsonb codec + pre-serialized writes corrupted metadata into arrays."""

from __future__ import annotations

import json

from taksitlio.search_sessions.postgres import _as_list, _as_object_dict, _jsonb


def test_as_object_dict_recovers_corrupt_array_metadata() -> None:
    need = {"need_state": {"budget": {"value": 40000}}, "logos": {}}
    corrupt = [{}, json.dumps(need)]
    assert _as_object_dict(corrupt) == need


def test_as_object_dict_accepts_object_and_json_string() -> None:
    assert _as_object_dict({"a": 1}) == {"a": 1}
    assert _as_object_dict('{"a": 1}') == {"a": 1}
    assert _as_object_dict(None) == {}


def test_jsonb_does_not_double_encode_dicts() -> None:
    payload = {"need_state": {"budget": {"value": 1}}}
    assert _jsonb(payload) is payload
    # Accidental pre-serialized string is unwrapped to object.
    assert _jsonb(json.dumps(payload)) == payload


def test_as_list_handles_json_string_arrays() -> None:
    assert _as_list([{"id": "x"}]) == [{"id": "x"}]
    assert _as_list('[{"id": "x"}]') == [{"id": "x"}]
    assert _as_list(None) == []
