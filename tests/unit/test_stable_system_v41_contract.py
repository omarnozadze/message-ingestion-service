"""Safety contract from one-c-stable-system-v41, pointed at the live source tree.

These guards protect decisions that were expensive to learn in production:
no automatic Unpost, a single Post call, plan-aware review shortlists and the
exact-identity matching rules.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_matching_engine_keeps_review_api_and_exact_identity_rules() -> None:
    text = _read("app/services/matching/engine.py")
    assert "def rank_for_review(" in text
    assert 'technical_prefixes = {"შმს", "შპს"}' in text
    assert 'if " * " in raw' in text


def test_odata_never_unposts_automatically() -> None:
    text = _read("app/integrations/one_c/odata.py")
    assert "/Unpost?" not in text
    assert "post_retry_start" not in text
    assert "LARGE_PLAN_POST_TIMEOUT_SECONDS" in text


def test_review_choices_use_active_plan_context() -> None:
    text = _read("app/api/routes/incoming_messages.py")
    assert "get_active_plan_partner_refs" in text
    assert "preferred_one_c_refs=preferred_one_c_refs" in text


def test_single_choice_prompt_is_not_called_multiple() -> None:
    text = _read("whatsapp-bridge/src/index.js")
    assert "choices.length === 1" in text
    assert "ერთი შესაძლო კლიენტი მოიძებნა" in text
