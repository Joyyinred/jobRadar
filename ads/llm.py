"""
Everything about talking to the LLM: the prompt, the real DeepSeek call, and
the mock. The rest of the app only calls call_llm(raw_text) and gets a dict
back — it doesn't know or care which model, SDK or prompt is behind it.

Moved here unchanged from views.py on Day 7.
"""
import json
import logging
import os
import time

import anthropic

from . import metrics

# __name__ is "ads.llm", so it inherits the "ads" logger from settings.LOGGING.
logger = logging.getLogger(__name__)

MODEL = "deepseek-v4-flash"

SYSTEM_PROMPT = """You extract structured data from one job ad.
The ad may be in Swedish or English. Always answer in English.

Return ONLY a JSON object, no markdown, no code fences, no explanation,
with exactly these keys:

{
  "title": string,                  // the job title as the ad states it
  "company": string,                // the hiring company
  "city": string,                   // where the job is; "remote" if fully remote
  "seniority": "intern" | "junior" | "mid" | "senior" | "unspecified",
  "required_skills": [string],      // technical skills the ad REQUIRES
  "nice_to_have_skills": [string],  // skills listed as a plus / meriterande
  "swedish_requirement": "required" | "preferred" | "not_mentioned"
}

A skill goes in exactly one of the two lists, never both.
Use short, common skill names ("Git", not "version control systems").
If the ad does not say something, use "unspecified" / "not_mentioned" / [].
Do not guess.
"""


def mock_llm(raw_text):
    """A fake LLM: returns a fixed answer in exactly the shape the real one does.

    Tests the whole pipeline (queue -> worker -> database -> polling) without
    paying for or waiting on DeepSeek. It does NOT test the prompt or how good
    the extraction is — only the real model can tell you that.
    """
    # Put MOCK_FAIL anywhere in the ad to make the "LLM" fail — a free way to
    # watch Celery's retries and the "failed" status.
    if "MOCK_FAIL" in raw_text:
        raise RuntimeError("mock LLM: simulated failure (ad contains MOCK_FAIL)")

    logger.info("LLM_MODE=mock: returning a fixed answer, no API call")
    return {
        "title": "Mock Junior Backend Developer",
        "company": "Mock Company AB",
        "city": "Stockholm",
        "seniority": "junior",
        "required_skills": ["Python", "Django", "PostgreSQL"],
        "nice_to_have_skills": ["Docker", "AWS"],
        "swedish_requirement": "preferred",
    }


def call_llm(raw_text):
    """Send the ad to DeepSeek and return the parsed JSON as a dict.

    Raises on any failure (network, bad key, non-JSON answer) — the caller
    decides what to do with that.

    Day 11: this wrapper times every call and counts successes and failures;
    the call itself is _call_llm().
    """
    mode = "mock" if os.environ.get("LLM_MODE", "real") == "mock" else "real"
    started = time.perf_counter()
    try:
        result = _call_llm(raw_text)
    except Exception:
        metrics.LLM_REQUESTS.labels(mode, "failure").inc()
        raise
    finally:
        # Every call, success or not: a slow failure is still slow.
        metrics.LLM_DURATION.labels(mode).observe(time.perf_counter() - started)
    metrics.LLM_REQUESTS.labels(mode, "success").inc()
    return result


def _call_llm(raw_text):
    # Day 4 experiment: pretend DeepSeek is having a slow day.
    # Set LLM_FAKE_DELAY=15 in .env to add 15 seconds to every call. Unset = 0.
    fake_delay = float(os.environ.get("LLM_FAKE_DELAY", "0"))
    if fake_delay:
        logger.info("LLM_FAKE_DELAY: sleeping %.0fs to simulate a slow LLM", fake_delay)
        time.sleep(fake_delay)

    # Day 6: LLM_MODE=mock in .env -> no API call at all (free, offline, same
    # answer every time). Anything else, or unset -> the real DeepSeek call.
    # Default is real on purpose: a forgotten setting in production must not
    # silently return fake data.
    if os.environ.get("LLM_MODE", "real") == "mock":
        return mock_llm(raw_text)

    # DeepSeek exposes an Anthropic-compatible endpoint, so we use the anthropic
    # SDK and just point it at DeepSeek. Switching to Claude = remove base_url,
    # use ANTHROPIC_API_KEY, and change MODEL.
    client = anthropic.Anthropic(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com/anthropic",
    )
    message = client.messages.create(
        model=MODEL,
        # No "thinking" for this job. Measured on a long ad: thinking on took
        # 20s and ~4,800 tokens (so it overran a 4,096 limit and failed) and
        # produced a worse skill list; thinking off took 1s and 144 tokens.
        # Extraction is reading, not reasoning.
        thinking={"type": "disabled"},
        max_tokens=4096,  # the answer alone is a few hundred tokens
        # temperature 0 = least random: the same ad gives the same answer.
        # Without it, one ad parsed 7 times gave 6-11 skills, once none at all.
        # This SDK version has no temperature argument, so it goes in the raw
        # request body; DeepSeek still reads it.
        extra_body={"temperature": 0},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": raw_text}],
    )
    # What this call costs. Recorded before parsing: tokens are billed even
    # when the answer turns out not to be valid JSON.
    metrics.LLM_TOKENS.labels("input").inc(message.usage.input_tokens)
    metrics.LLM_TOKENS.labels("output").inc(message.usage.output_tokens)

    # The reply is a list of blocks. With thinking on, a "thinking" block comes
    # before the "text" answer; filtering by type works either way.
    texts = [block.text for block in message.content if block.type == "text"]
    if not texts:
        # Say why in the log, instead of a bare StopIteration.
        raise ValueError(f"LLM returned no text block (stop_reason={message.stop_reason})")
    text = texts[0].strip()

    # Models sometimes wrap JSON in ```json ... ``` despite being told not to.
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()

    # If this isn't valid JSON, json.loads raises and the ad is marked failed.
    # (No check that the keys/values are right — that's Day 8.)
    return json.loads(text)
