"""
Day 11: every JobRadar metric, defined in one place.

Prometheus metric types used here:
  Counter   — only goes up ("how many so far"). Graphs use rate()/increase()
              to turn it into "per minute" / "in the last hour".
  Histogram — counts observations into buckets (<=0.5s, <=1s, ...), which is
              what lets Grafana compute p50 / p95 instead of a misleading average.
  Gauge     — a current value that goes up and down ("how many right now").

Where each metric is recorded matters: web and worker are separate processes,
and each exposes only its own numbers.
  web    (/metrics on :8000) — ingest, JobTech, plus Django's HTTP metrics
                               from django-prometheus
  worker (:9100)             — LLM calls, tokens, parse results
  both                       — queue depth (read from Redis on each scrape,
                               so both report the same number; the dashboard
                               reads the web one)
Prometheus scrapes both (monitoring/prometheus.yml).

Labels only ever take a handful of fixed values (source, result, ...) —
never ids or text: every distinct label value becomes its own time series.
"""
import redis
from django.conf import settings
from prometheus_client import Counter, Gauge, Histogram

# --- Ingest (web) -------------------------------------------------------------

INGEST = Counter(
    "jobradar_ingest_total",
    "Ads offered to services.ingest(), by source and what happened to them.",
    ["source", "result"],  # result: created | duplicate | warning | invalid
)

# --- JobTech API (web) --------------------------------------------------------

JOBTECH_REQUESTS = Counter(
    "jobradar_jobtech_requests_total",
    "Calls to the JobTech API.",
    ["result"],  # ok | not_found | error
)

# --- LLM (worker) -------------------------------------------------------------

LLM_REQUESTS = Counter(
    "jobradar_llm_requests_total",
    "LLM calls.",
    ["mode", "result"],  # mode: real | mock ; result: success | failure
)
LLM_DURATION = Histogram(
    "jobradar_llm_request_duration_seconds",
    "How long one LLM call took.",
    ["mode"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)
LLM_TOKENS = Counter(
    "jobradar_llm_tokens_total",
    "Tokens used by real LLM calls — what the bill is made of.",
    ["kind"],  # input | output
)

# --- Parsing outcome (worker) -------------------------------------------------

PARSE = Counter(
    "jobradar_parse_total",
    "Parse attempts by outcome.",
    ["result"],  # completed | retried | failed
)
AD_PARSE_SECONDS = Histogram(
    "jobradar_ad_time_to_parsed_seconds",
    "From an ad being saved to it being parsed: queue wait + LLM + retries. "
    "What the user actually waits.",
    buckets=(1, 2, 5, 10, 30, 60, 120, 300, 600, 1800),
)

# --- Start every known series at 0 ---------------------------------------------
#
# A labelled counter only appears once it's first used. If the first thing
# Prometheus ever sees is "completed = 3", it can't tell that went up from 0,
# and increase()/rate() report 0 — graphs and the success rate stay empty.
# Creating each known label combination up front (value 0) means every later
# step up is seen. Same reason the token panel shows 0 rather than "No data".

for _source in ("manual_paste", "jobtech_api"):
    for _result in ("created", "duplicate", "warning", "invalid"):
        INGEST.labels(_source, _result)
for _result in ("ok", "not_found", "error"):
    JOBTECH_REQUESTS.labels(_result)
for _mode in ("real", "mock"):
    LLM_DURATION.labels(_mode)
    for _result in ("success", "failure"):
        LLM_REQUESTS.labels(_mode, _result)
for _kind in ("input", "output"):
    LLM_TOKENS.labels(_kind)
for _result in ("completed", "retried", "failed"):
    PARSE.labels(_result)

# --- Queue (web, read at scrape time) -----------------------------------------

QUEUE_DEPTH = Gauge(
    "jobradar_queue_depth",
    "Parse tasks waiting in the Redis queue right now (not yet picked up by a worker).",
)


def _queue_length():
    # "celery" is the Redis list Celery keeps its default queue in. Read on
    # every scrape, so the number is always current. If Redis is down, report
    # -1 rather than break the whole /metrics page.
    try:
        return redis.Redis.from_url(
            settings.REDIS_URL, socket_timeout=1, socket_connect_timeout=1,
        ).llen("celery")
    except redis.RedisError:
        return -1


QUEUE_DEPTH.set_function(_queue_length)
