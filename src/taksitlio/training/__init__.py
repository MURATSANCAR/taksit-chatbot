"""Training helpers package (P17 scaffold — no GPU execution claimed)."""

from taksitlio.training.export_sft import (
    DEFAULT_SYSTEM_PROMPT,
    build_sft_row,
    iter_golden_sft_rows,
    iter_hr_validation_sft_rows,
    need_profile_from_golden_expected,
    need_profile_from_hr_constraints,
    write_jsonl,
)

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "build_sft_row",
    "iter_golden_sft_rows",
    "iter_hr_validation_sft_rows",
    "need_profile_from_golden_expected",
    "need_profile_from_hr_constraints",
    "write_jsonl",
]
