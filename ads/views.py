"""
JobRadar MVP — everything in one file on purpose.

Flow (Day 4): paste ad -> save raw (pending) -> put its id on the Redis queue
             -> return right away.

What this deliberately does NOT do yet (each is a later day):
  - no worker                 (Day 5: nobody takes ids off the queue yet, so
                               every new ad stays "pending" forever)
  - no polling                (Day 6)
  - no layering               (Day 7: Controller / Service / Repository)
  - no validation, no dedup,  (Day 8)
    no tests
"""
import json
import logging
import os
import time

import anthropic
import redis
from django.conf import settings
from django.shortcuts import render
from rest_framework import status as http_status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import AdSkill, Company, JobAd, Skill

# One connection pool for the whole process, reused by every request.
queue = redis.Redis.from_url(settings.REDIS_URL)

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


def call_llm(raw_text):
    """Send the ad to DeepSeek and return the parsed JSON as a dict.

    Raises on any failure (network, bad key, non-JSON answer) — the caller
    decides what to do with that.
    """
    # Day 4 experiment: pretend DeepSeek is having a slow day.
    # Set LLM_FAKE_DELAY=15 in .env to add 15 seconds to every call. Unset = 0.
    fake_delay = float(os.environ.get("LLM_FAKE_DELAY", "0"))
    if fake_delay:
        logger.info("LLM_FAKE_DELAY: sleeping %.0fs to simulate a slow LLM", fake_delay)
        time.sleep(fake_delay)

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


def save_parsed(ad, data):
    """Split the LLM's one JSON object across the tables.

    data looks like {"title": ..., "company": ..., "required_skills": [...], ...}
    """
    # Company: reuse the row if this exact name exists, otherwise create it.
    # get_or_create returns (object, created?) — we only need the object.
    company_name = (data.get("company") or "").strip()
    if company_name and company_name != "unspecified":
        ad.company, _ = Company.objects.get_or_create(name=company_name)

    # Per-ad fields: plain columns on JobAd.
    ad.title = data.get("title") or ""
    ad.city = data.get("city") or ""
    ad.seniority = data.get("seniority") or ""
    ad.swedish_requirement = data.get("swedish_requirement") or ""
    ad.save()

    # Skills: one Skill row per name (shared by all ads), plus one AdSkill row
    # per (this ad, that skill) saying how much the ad wants it.
    for level, key in [("required", "required_skills"), ("nice_to_have", "nice_to_have_skills")]:
        for name in data.get(key) or []:
            skill, _ = Skill.objects.get_or_create(name=name.strip())
            # get_or_create again: if the LLM lists a skill twice, the
            # unique (ad, skill) constraint would otherwise reject the 2nd row.
            AdSkill.objects.get_or_create(ad=ad, skill=skill, defaults={"level": level})


def ad_result(ad):
    """The reverse of save_parsed: gather the tables back into one dict.

    Same shape the API returned on Day 2, so the frontend didn't change.
    """
    ad_skills = ad.ad_skills.select_related("skill")  # all AdSkill rows of this ad
    return {
        "title": ad.title,
        "company": ad.company.name if ad.company else None,
        "city": ad.city,
        "seniority": ad.seniority,
        "required_skills": [s.skill.name for s in ad_skills if s.level == "required"],
        "nice_to_have_skills": [s.skill.name for s in ad_skills if s.level == "nice_to_have"],
        "swedish_requirement": ad.swedish_requirement,
    }


def index(request):
    """Serve the one HTML page. The page talks to the API with fetch()."""
    return render(request, "ads/index.html")


@api_view(["GET", "POST"])
def ads_collection(request):
    """/api/ads/ — one URL, two actions. The HTTP method decides which."""
    if request.method == "GET":
        return list_ads(request)
    return create_ad(request)


def list_ads(request):
    """GET /api/ads/ — a short summary of every ad, newest first."""
    # select_related("company") fetches each ad's company in the SAME query
    # (a SQL JOIN). Without it, ad.company.name below would run one extra
    # query per ad: 100 ads = 101 queries. That's called the "N+1 problem".
    ads = JobAd.objects.select_related("company").order_by("-created_at")

    summaries = []
    for ad in ads:
        # Compare with Day 2: plain columns now, no digging inside JSON.
        summaries.append({
            "id": ad.id,
            "status": ad.status,
            "title": ad.title or None,
            "company": ad.company.name if ad.company else None,
        })
    # No pagination: 1,000 ads = 1,000 rows in one response. Fine for now.
    return Response(summaries)


def create_ad(request):
    """POST /api/ads/   body: {"raw_text": "..."}"""
    # No validation on purpose — whatever arrives, we use. (Day 8)
    raw_text = request.data.get("raw_text", "")

    # 1. Save the raw ad, status pending. The database is the source of truth.
    ad = JobAd.objects.create(raw_text=raw_text, status="pending")
    logger.info("ad %s saved (%d chars)", ad.id, len(raw_text))  # ids only, never ad text

    # 2. Put ONLY the id on the queue — the ad itself is already in the database.
    #    LPUSH adds to the left end of a Redis list; whoever takes jobs later
    #    pops from the right end, so the oldest ad comes out first (FIFO).
    queue.lpush(settings.PARSE_QUEUE, ad.id)
    logger.info("ad %s queued", ad.id)

    # 3. Return immediately. No LLM call here any more: call_llm() and
    #    save_parsed() above are untouched, waiting for tomorrow's worker.
    #    202 Accepted = "got it, not done yet" (201 Created meant "done").
    return Response({"id": ad.id, "status": ad.status}, status=http_status.HTTP_202_ACCEPTED)


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
        body["result"] = ad_result(ad)
    elif ad.status == "failed":
        body["error"] = ad.error
    return Response(body)  # 200 OK is the default
