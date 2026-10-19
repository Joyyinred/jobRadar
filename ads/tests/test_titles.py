"""
English titles: from the parse for new ads, from `translate_titles` for old ones.
"""
import pytest
from django.core.management import call_command

from ads import llm, services
from ads.models import JobAd
from ads.tests.test_services import LONG_AD, make_ad

pytestmark = pytest.mark.django_db


def parsed_ad(n, title, title_en=""):
    return make_ad(raw_text=f"{LONG_AD} {n}", status="completed", title=title, title_en=title_en)


# --- From the parse ---------------------------------------------------------------

def test_parse_stores_both_titles():
    ad = make_ad()
    services.save_parsed(ad, {"title": "Systemutvecklare", "title_en": "Systems Developer"})
    ad.refresh_from_db()
    assert (ad.title, ad.title_en) == ("Systemutvecklare", "Systems Developer")


def test_no_title_en_from_the_model_falls_back_to_the_title():
    ad = make_ad()
    services.save_parsed(ad, {"title": "Systemutvecklare"})
    ad.refresh_from_db()
    assert ad.title_en == "Systemutvecklare"  # never blank in the list


# --- translate_titles: the LLM side -----------------------------------------------

def test_translate_titles_rejects_an_answer_of_the_wrong_length(monkeypatch):
    """39 titles back for 40 would put titles next to the wrong ads."""
    monkeypatch.setenv("LLM_MODE", "real")
    monkeypatch.setattr(llm, "_ask_for_json", lambda system, content: ["only one"])
    with pytest.raises(ValueError):
        llm.translate_titles(["Systemutvecklare", "Testare"])


def test_translate_titles_returns_the_models_list(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "real")
    monkeypatch.setattr(llm, "_ask_for_json", lambda system, content: ["Systems Developer", "Tester"])
    assert llm.translate_titles(["Systemutvecklare", "Testare"]) == ["Systems Developer", "Tester"]


# --- translate_titles: the command ------------------------------------------------

@pytest.fixture
def fake_translate(monkeypatch):
    """A "translation" that marks each title, and records every batch sent."""
    batches = []

    def translate(titles):
        batches.append(list(titles))
        return [f"EN {t}" for t in titles]

    monkeypatch.setattr(llm, "translate_titles", translate)
    return batches


def test_command_fills_only_ads_without_an_english_title(fake_translate):
    todo = parsed_ad(1, "Systemutvecklare")
    done = parsed_ad(2, "Testare", title_en="Tester")
    make_ad(raw_text=f"{LONG_AD} pending")  # not parsed yet: no title to translate
    call_command("translate_titles")
    todo.refresh_from_db(), done.refresh_from_db()
    assert (todo.title_en, done.title_en) == ("EN Systemutvecklare", "Tester")
    assert fake_translate == [["Systemutvecklare"]]


def test_command_sends_titles_in_batches(fake_translate, monkeypatch):
    from ads.management.commands import translate_titles
    monkeypatch.setattr(translate_titles, "BATCH", 2)
    for n in range(5):
        parsed_ad(n, f"Titel {n}")
    call_command("translate_titles")
    assert [len(b) for b in fake_translate] == [2, 2, 1]
    assert not JobAd.objects.filter(title_en="").exists()


def test_a_failed_batch_is_skipped_and_the_rest_still_done(monkeypatch):
    from ads.management.commands import translate_titles
    monkeypatch.setattr(translate_titles, "BATCH", 1)

    def translate(titles):
        if titles == ["Trasig"]:
            raise ValueError("wrong length")
        return [f"EN {t}" for t in titles]

    monkeypatch.setattr(llm, "translate_titles", translate)
    bad, good = parsed_ad(1, "Trasig"), parsed_ad(2, "Hel")
    call_command("translate_titles")
    bad.refresh_from_db(), good.refresh_from_db()
    assert (bad.title_en, good.title_en) == ("", "EN Hel")  # the next run retries the bad one


def test_dry_run_calls_no_llm(fake_translate):
    parsed_ad(1, "Systemutvecklare")
    call_command("translate_titles", dry_run=True)
    assert fake_translate == []
