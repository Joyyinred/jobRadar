"""
Unit tests: CreateAdSerializer — the checks that need only the request itself
(no database). Each test feeds a body in and looks at is_valid() / errors.
"""
from ads.serializers import MAX_AD_CHARS, CreateAdSerializer


def check(body):
    serializer = CreateAdSerializer(data=body)
    return serializer.is_valid(), serializer


def test_valid_minimal_body():
    ok, s = check({"raw_text": "An ad"})
    assert ok
    assert s.validated_data == {"raw_text": "An ad", "confirm": False}


def test_raw_text_is_required():
    ok, s = check({})
    assert not ok
    assert "raw_text" in s.errors


def test_empty_raw_text_is_rejected():
    ok, s = check({"raw_text": ""})
    assert not ok
    assert "raw_text" in s.errors


def test_spaces_only_is_rejected():
    ok, s = check({"raw_text": "   \n\t "})
    assert not ok
    assert s.errors["raw_text"] == ["The ad is empty."]


def test_exactly_max_length_is_accepted():
    ok, _ = check({"raw_text": "a" * MAX_AD_CHARS})
    assert ok


def test_one_over_max_length_is_rejected_not_truncated():
    ok, s = check({"raw_text": "a" * (MAX_AD_CHARS + 1)})
    assert not ok
    assert "raw_text" in s.errors


def test_raw_text_is_not_trimmed():
    # The raw ad is stored exactly as pasted.
    ok, s = check({"raw_text": "  spaced  "})
    assert ok
    assert s.validated_data["raw_text"] == "  spaced  "


def test_bad_url_is_rejected():
    ok, s = check({"raw_text": "An ad", "source_url": "not a url"})
    assert not ok
    assert "source_url" in s.errors


def test_good_url_is_kept():
    ok, s = check({"raw_text": "An ad", "source_url": "https://example.com/jobs/1"})
    assert ok
    assert s.validated_data["source_url"] == "https://example.com/jobs/1"


def test_empty_url_becomes_none():
    # "" would collide with other "" under the unique constraint; None doesn't.
    ok, s = check({"raw_text": "An ad", "source_url": ""})
    assert ok
    assert s.validated_data["source_url"] is None


def test_both_fields_wrong_reports_both():
    ok, s = check({"raw_text": " ", "source_url": "nope"})
    assert not ok
    assert set(s.errors) == {"raw_text", "source_url"}
