"""
Unit tests: normalise() and content_hash_of(). Pure functions — no database,
no network. These decide what counts as "the same ad".
"""
from ads.services import content_hash_of, normalise


def test_hash_is_64_hex_characters():
    h = content_hash_of("Java dev wanted")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_same_text_gives_same_hash():
    assert content_hash_of("Java dev wanted") == content_hash_of("Java dev wanted")


def test_extra_spaces_and_newlines_give_same_hash():
    # Copy-paste noise must not make it a "different" ad.
    assert content_hash_of("  Java   dev\n wanted  ") == content_hash_of("Java dev wanted")


def test_windows_line_endings_give_same_hash():
    assert content_hash_of("Java\r\ndev") == content_hash_of("Java\ndev")


def test_one_character_difference_gives_different_hash():
    assert content_hash_of("Java dev wanted.") != content_hash_of("Java dev wanted")


def test_case_matters():
    # Deliberate: normalise() only touches whitespace, not letters.
    assert content_hash_of("java dev wanted") != content_hash_of("Java dev wanted")


def test_normalise_collapses_whitespace_and_trims():
    assert normalise("  a \t b\r\n\n c  ") == "a b c"


def test_hash_length_is_fixed_for_a_long_ad():
    assert len(content_hash_of("x" * 50_000)) == 64
