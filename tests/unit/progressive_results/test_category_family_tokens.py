"""Category family-token regression — data-driven, not hardwired to example words."""

from __future__ import annotations

from taksitlio.progressive_results.category_match import (
    CATEGORY_FAMILIES,
    _include_tokens_for,
    matches_category_tokens,
    reload_category_family_tokens,
)


def test_family_tokens_loaded_from_data_file() -> None:
    reload_category_family_tokens()
    assert "category-laptop" in CATEGORY_FAMILIES
    assert "laptop" in CATEGORY_FAMILIES["category-laptop"]["include"]
    assert "dizüstü" in CATEGORY_FAMILIES["category-laptop"]["include"] or any(
        "dizüst" in t for t in CATEGORY_FAMILIES["category-laptop"]["include"]
    )


def test_positive_family_token_recall_union() -> None:
    reload_category_family_tokens()
    cat = {
        "resolved_id": "category-laptop",
        "display_name": "Dizüstü Bilgisayar",
        "include_tokens": ["laptop"],
    }
    tokens = _include_tokens_for(cat)
    assert "laptop" in tokens
    # Family union must retain Turkish synonym coverage
    assert any("dizüst" in t or t == "notebook" for t in tokens)
    product = {
        "display_name": "Lenovo Dizüstü Bilgisayar 16GB",
        "category_name": "Dizüstü Bilgisayar",
    }
    assert matches_category_tokens(product, cat) is True


def test_negative_category_exclusion() -> None:
    reload_category_family_tokens()
    cat = {"resolved_id": "category-laptop", "display_name": "Laptop"}
    phone = {"display_name": "iPhone 15 128GB", "category_name": "Cep Telefonu"}
    assert matches_category_tokens(phone, cat) is False


def test_overlay_alias_version_without_code_edit() -> None:
    reload_category_family_tokens(
        overlay={
            "category-widget": {
                "include": ("widgetron", "widjet"),
                "exclude": ("phone",),
            }
        }
    )
    assert matches_category_tokens(
        {"display_name": "Super Widgetron X"},
        {"resolved_id": "category-widget", "include_tokens": ["widjet"]},
    )
    assert not matches_category_tokens(
        {"display_name": "Phone case"},
        {"resolved_id": "category-widget"},
    )
    reload_category_family_tokens()  # restore


def test_cross_category_conflict_no_false_inclusion() -> None:
    reload_category_family_tokens()
    laptop_cat = {"resolved_id": "category-laptop", "display_name": "Laptop"}
    assert not matches_category_tokens(
        {"display_name": "Samsung Galaxy S24 Cep Telefonu"},
        laptop_cat,
    )
