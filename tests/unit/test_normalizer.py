from app.services.matching.normalizer import normalize_text


def test_normalize_text_handles_whitespace_and_punctuation() -> None:
    assert normalize_text("  გვირილა,   0043! ") == "გვირილა 0043"


def test_normalize_text_casefolds_latin() -> None:
    assert normalize_text("GVIRELA 0043") == "gvirela 0043"
