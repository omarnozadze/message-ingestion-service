from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.services.matching.engine import MatchingEngine
from app.services.matching.types import MatchCandidate

ROOT = Path(__file__).resolve().parents[2]


def _candidate(name: str) -> MatchCandidate:
    return MatchCandidate(
        client_id=uuid4(),
        code=None,
        display_name=name,
        normalized_name=name,
        aliases=(),
        one_c_ref=None,
    )


def test_choices_endpoint_is_present_and_local_only() -> None:
    source = (ROOT / 'app/api/routes/incoming_messages.py').read_text(encoding='utf-8')
    assert '/incoming-message/{message_id}/choices' in source
    assert 'rank_for_review' in source
    assert 'client_id' in source


def test_match_diagnostics_are_audited() -> None:
    source = (ROOT / 'app/services/incoming_message_service.py').read_text(encoding='utf-8')
    assert 'MATCH_DIAGNOSTICS' in source
    assert 'normalized_query' in source
    assert 'top_candidates' in source


def test_whatsapp_bridge_has_number_choice_flow() -> None:
    source = (ROOT / 'whatsapp-bridge/src/index.js').read_text(encoding='utf-8')
    assert 'pendingChoicesByPhone' in source
    assert 'parseChoiceNumber' in source
    assert 'resolvePendingChoice' in source
    assert 'PENDING_CHOICE_TTL_MS' in source


def test_review_ranking_keeps_prefixed_retail_branches_distinct() -> None:
    engine = MatchingEngine()
    s278 = _candidate('რითეილ ჯგუფი *S278 - ბორჯომი, წაღვერი')
    k278 = _candidate('რითეილ ჯგუფი *K278 - თბილისი')
    d278 = _candidate('რითეილ ჯგუფი *D278 - ბათუმი')

    ranked = engine.rank_for_review('რითეილ S278', [k278, d278, s278], limit=3)

    assert ranked
    assert ranked[0].client_id == s278.client_id
    assert all(
        item.client_id != k278.client_id or item.score < ranked[0].score
        for item in ranked
    )
