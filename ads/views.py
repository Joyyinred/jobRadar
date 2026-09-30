"""
JobRadar MVP — everything in one file on purpose.

Flow: paste ad -> save raw -> call the LLM (blocking) -> save fields -> return JSON.

What this deliberately does NOT do yet (each is a later day):
  - one table only            (Day 3: split into proper tables)
  - no queue / worker         (Day 4-5: the request waits for the whole LLM call)
  - no polling                (Day 6)
  - no layering               (Day 7: Controller / Service / Repository)
  - no validation, no dedup,  (Day 8)
    no tests
"""
import json
import logging
import os

import anthropic
from django.shortcuts import render
from rest_framework import status as http_status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import JobAd

# __name__ is "ads.views", so it inherits the "ads" logger from settings.LOGGING.
logger = logging.getLogger(__name__)

MODEL = "deepseek-v4-flash"

SYSTEM_PROMPT = """You extract structured data from one job ad.
The ad may be in Swedish or English. Always answer in English.

Return ONLY a JSON object, no markdown, no code fences, no explanation,
with exactly these keys:

{
  "title": string,                  // the job title as the ad states it
  "company": string,                // the hiring company
  "seniority": "intern" | "junior" | "mid" | "senior" | "unspecified",
  "required_skills": [string],      // technical skills the ad REQUIRES (not nice-to-have)
  "swedish_requirement": "required" | "preferred" | "not_mentioned"
}

If the ad does not say something, use "unspecified" / "not_mentioned" / [].
Do not guess.
"""


def call_llm(raw_text):
    """Send the ad to DeepSeek and return the parsed JSON as a dict.

    Raises on any failure (network, bad key, non-JSON answer) — the caller
    decides what to do with that.
    """
    # DeepSeek exposes an Anthropic-compatible endpoint, so we use the anthropic
    # SDK and just point it at DeepSeek. Switching to Claude = remove base_url,
    # use ANTHROPIC_API_KEY, and change MODEL.
    client = anthropic.Anthropic(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com/anthropic",
    )
    message = client.messages.create(
        model=MODEL,
        # The limit covers thinking AND the answer. At 1024 a 2,400-char ad used
        # it all on thinking and never wrote the JSON (stop_reason "max_tokens").
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": raw_text}],
    )
    # The reply is a list of blocks. This model sends a "thinking" block first,
    # then the answer as a "text" block — take the text one.
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


def index(request):
    """Serve the one HTML page. The page talks to the API with fetch()."""
    return render(request, "ads/index.html")


@api_view(["POST"])
def create_ad(request):
    """POST /api/ads/   body: {"raw_text": "..."}"""
    # No validation on purpose — whatever arrives, we use. (Day 8)
    raw_text = request.data.get("raw_text", "")

    # 1. Save the raw ad first, so it exists even if the LLM call fails.
    ad = JobAd.objects.create(raw_text=raw_text, status="pending")
    logger.info("ad %s saved (%d chars)", ad.id, len(raw_text))  # ids only, never ad text

    # 2. Mark it processing.
    ad.status = "processing"
    ad.save()

    # 3. The blocking call. The browser sits here until DeepSeek answers.
    try:
        logger.info("ad %s: calling LLM ... BLOCKING", ad.id)
        ad.result = call_llm(raw_text)
        ad.status = "completed"
        logger.info("ad %s: completed", ad.id)
    except Exception:
        # Full details go to the log for you; the user only gets a safe message.
        logger.exception("ad %s: LLM call failed", ad.id)
        ad.status = "failed"
        ad.error = "Could not parse this ad. Please try again."
    ad.save()

    # 4. Return what the API design said: id, status, and result or error.
    body = {"id": ad.id, "status": ad.status}
    if ad.status == "completed":
        body["result"] = ad.result
    else:
        body["error"] = ad.error
    return Response(body, status=http_status.HTTP_201_CREATED)


@api_view(["GET"])  # only GET is allowed here; a POST to this URL gets 405
def get_ad(request, ad_id):
    """GET /api/ads/<ad_id>/"""
    # 1. Find the row. .get() returns exactly one ad, or raises DoesNotExist.
    try:
        ad = JobAd.objects.get(id=ad_id)
    except JobAd.DoesNotExist:
        # 3. The id doesn't exist -> 404 "not found", with a clear message.
        return Response(
            {"error": f"Ad {ad_id} not found"},
            status=http_status.HTTP_404_NOT_FOUND,
        )

    # 2. Same shape as the POST response: id, status, and result or error.
    #    (Yes, this is copy-pasted from create_ad. Remember that for Day 7.)
    #    pending / processing -> only id + status, nothing else to show yet.
    body = {"id": ad.id, "status": ad.status}
    if ad.status == "completed":
        body["result"] = ad.result
    elif ad.status == "failed":
        body["error"] = ad.error
    return Response(body)  # 200 OK is the default
