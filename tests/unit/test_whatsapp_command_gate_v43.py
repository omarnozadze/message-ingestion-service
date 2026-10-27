"""Regression tests for the v43 WhatsApp command gate.

The gate lives in whatsapp-bridge/src/index.js. These tests read the real
regular expressions from that file (instead of a hand-copied duplicate), so a
change to the bridge is caught by the Python test suite.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parents[2] / "whatsapp-bridge" / "src" / "index.js"


def _js_regex(name: str) -> re.Pattern[str]:
    source = BRIDGE.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = /(.+)/([a-z]*)\s*$", source, flags=re.MULTILINE)
    assert match, f"{name} not found in {BRIDGE}"
    flags = re.IGNORECASE if "i" in match.group(2) else 0
    return re.compile(match.group(1), flags)


DAY = _js_regex("VISIT_COMMAND_DAY_RE")
ACTION = _js_regex("VISIT_COMMAND_ACTION_RE")
PLAN = _js_regex("VISIT_COMMAND_PLAN_RE")
CODE = _js_regex("VISIT_COMMAND_CLIENT_CODE_RE")


def looks_like_visit_command(text: str) -> bool:
    """Python mirror of looksLikeVisitCommand() in index.js."""
    value = text.strip()
    if not value or not ACTION.search(value):
        return False
    return bool(DAY.search(value) or PLAN.search(value) or CODE.search(value))


@pytest.mark.parametrize(
    "text",
    [
        "110 გვირილა #0296 მხოლოდ ოთხშაბათზე ჩასვი",
        "111 გვირილა 0133 გვირილა 0018 რითეილ 238 ჩასვი სამშაბათზე",
        "111 გვირილა 0231, რითეილ 2322, რითეილ K223 ჩასვი სამშაბათზე",
        "115 რითეილ S225 პარასკევზე ჩასვი",
        "115 რითეილ S225 გეგმიდან ამოიღე",
        "115 რითეილ S225 ორშაბათიდან პარასკევზე გადაიტანე",
    ],
)
def test_visit_commands_are_forwarded(text: str) -> None:
    assert looks_like_visit_command(text)


@pytest.mark.parametrize(
    "text",
    [
        "ომარ სალამი",
        "გამიმოთე რა ლოკაციები",
        "შეგულებაზე ვარ",
        "სალამი კი გათიშულია",
        "110 სალამი",
        "ფილიალი აქ არ არის",
        "",
    ],
)
def test_ordinary_chat_is_ignored(text: str) -> None:
    assert not looks_like_visit_command(text)


def test_gate_marker_present() -> None:
    assert "whatsapp-command-gate-v43" in BRIDGE.read_text(encoding="utf-8")
