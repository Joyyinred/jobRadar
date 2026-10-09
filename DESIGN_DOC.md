# JobRadar — Design Doc (v1)

**Status:** Draft v1
**Last updated:** 2026-09-27
**Companion to:** [REQUIREMENTS_DOC.md](REQUIREMENTS_DOC.md)

---

## 1. Overview

JobRadar collects job ads from several sources, turns each ad into structured data,
tracks the ads I apply to, and aggregates the whole collection into statistics about
the Swedish junior tech job market.

One core record — the job ad — supports three features:

| Feature | Operates on |
|---|---|
| Application tracking | The ads I act on |
| Market analysis | Every ad collected |
| Gap analysis | My applications × my CV × market frequency |

---

## 2. Why this is not an LLM wrapper

The obvious objection: *a user can paste a job ad into Claude and get the same
extraction.* For a single ad, that is true, and this section says so plainly rather
than pretending otherwise.

### 2.1 What the LLM does here

Exactly one job: **turn a block of unstructured text into one structured row.** It does
not deduplicate, count, normalise, track state, or produce statistics.

### 2.2 What a chat interface cannot do

| Need | Why a chat conversation fails | What JobRadar does instead |
|---|---|---|
| **Counting** — "what share of junior backend ads ask for Java?" | Thousands of ads don't fit in a conversation. Even when they fit, an LLM asked to count produces a plausible-sounding number, not an exact one. | SQL `COUNT` over stored, structured rows. Exact. |
| **Consistency** | Parse ad #1 today and ad #200 next month: "Postgres" becomes "PostgreSQL", a requirement judged *required* becomes *nice-to-have*. Statistics over inconsistent extractions are meaningless. | Fixed output schema, pinned prompt and model version, skill normalisation in code via an alias table, extractions stored rather than regenerated. |
| **Memory across time** | Ads read over two months don't become a queryable collection. "Which kind of role gives me the best interview rate?" has no answer. | Every ad and every status change is persisted. |
| **Deduplication** | Recognising the same role posted on three sites under three titles requires seeing the whole collection and applying the same rule every time. | Deterministic dedup against the full dataset (§7). |
| **Automation** | Every ad has to be pasted by hand. | The JobTech adapter pulls ads on a schedule. |
| **Auditability** | A chat answer can't be traced back to its sources. | Every statistic traces to specific stored ads; raw text is kept, so every extraction can be re-run and checked. |

### 2.3 The test: delete the LLM — what remains?

Applied to the predecessor project (CarePlan): almost nothing. A form and a text box.
It *was* a wrapper.

Applied to JobRadar: the database, the adapters, deduplication, the application state
machine, the aggregations and the gap analysis all still work. Structured fields would
have to be filled in by hand instead of by a model. Nothing else changes.

**The LLM is a replaceable component. The system is not.** That is the line between a
wrapper and a system that happens to use an LLM.

### 2.4 An honest caveat

The non-wrapper value appears only once there is volume and deduplication — around
milestone M5 (§14). Everything built before that — paste one ad, see it parsed — *is* a
wrapper, and looks like one. The project justifies itself only if it gets past M5.

---

## 3. Goals and non-goals

### 3.1 Goals

- Ingest ads from paste, file upload, and the JobTech open API behind one interface.
- Store raw ads immutably; derive everything else from them.
- Extract structured fields asynchronously, with a validated schema and bounded retry.
- Deduplicate cross-posted roles, deterministically and testably.
- Track applications through an enforced state machine.
- Produce exact market statistics over **deduplicated roles**, with a minimum-sample
  rule.
- Run end to end with one command; cover the critical logic with tests.

### 3.2 Non-goals

- No scraping of LinkedIn, Indeed or any site whose terms forbid it.
- No real authentication beyond a single local user (Phase 2).
- No salary analysis — most Swedish ads don't state salary.
- No email/inbox integration.
- No mobile client. This is a desk tool; see REQUIREMENTS_DOC Assumption 7.
- No LLM-generated statistics or summaries in the MVP.

---

## 4. Data model

```
Company        1 ── N  Role
Role           1 ── N  JobAd              (cross-posts of the same real job)
JobAd          1 ── N  JobAdAnalysis      (one per extractor version)
JobAdAnalysis  1 ── N  AnalysisSkill
Skill          1 ── N  SkillAlias
Skill          1 ── N  AnalysisSkill
Role           1 ── 0..1 Application
Application    1 ── N  ApplicationEvent   (append-only history)
CV             1 ── N  CVSkill
```

### 4.1 The relationship that is hardest to change later

**An application points at a `Role`, not at a `JobAd`.**

The natural first design is `Application → JobAd`. It breaks as soon as the same role
exists as two ads: I can apply "twice", and every statistic counts the role twice. The
`Role` table represents *the real-world job*; `JobAd` represents *one posting of it*.
Retrofitting this after data exists means re-pointing every application and re-running
every aggregate. It is decided now.

For the same reason, **market statistics count roles, not ads.** A role posted on three
sites is one data point.

### 4.2 Company

| Field | Type | Rules |
|---|---|---|
| id | PK | |
| display_name | string | as first seen |
| normalized_name | string | **UNIQUE**; see §6.2 |

`CompanyAlias(alias UNIQUE → company)` maps variants (`"Klarna Bank AB"`,
`"Klarna AB (publ)"`) to one company. Seeded by hand, extended when a merge is
confirmed.

### 4.3 Role

| Field | Type | Rules |
|---|---|---|
| id | PK | |
| company | FK → Company | required |
| normalized_title | string | required |
| city | string | required; `"remote"` is a valid value |
| first_seen_at | timestamp | |

### 4.4 JobAd

| Field | Type | Rules |
|---|---|---|
| id | PK | |
| source | enum | `manual_paste` \| `file_upload` \| `jobtech_api` |
| source_external_id | string | nullable; **UNIQUE (source, source_external_id)** where not null |
| source_url | string | nullable; **UNIQUE** where not null |
| raw_content | text | **immutable** after insert |
| content_hash | char(64) | SHA-256 of normalised raw content; **UNIQUE** |
| role | FK → Role | nullable until dedup has run |
| dedup_status | enum | `pending` \| `new_role` \| `auto_linked` \| `needs_review` \| `confirmed` |
| parse_status | enum | `pending` \| `processing` \| `done` \| `failed` |
| parse_attempts | int | |
| ingested_at | timestamp | UTC |

The three UNIQUE constraints are the three layers of exact-duplicate protection (§7.1).
They live in the database, not only in code.

### 4.5 JobAdAnalysis

| Field | Type | Rules |
|---|---|---|
| id | PK | |
| job_ad | FK → JobAd | |
| extractor_version | string | **UNIQUE (job_ad, extractor_version)** |
| model / prompt_version | string | pinned, for traceability |
| is_current | bool | exactly one current analysis per ad |
| title_raw, company_raw, city_raw | string | exactly as the ad states them |
| seniority | enum | `intern` \| `junior` \| `mid` \| `senior` \| `unspecified` |
| employment_type | enum | `permanent` \| `fixed_term` \| `lia` \| `exjobb` \| `unspecified` |
| swedish_requirement | enum | `required` \| `preferred` \| `not_mentioned` |
| english_requirement | enum | same |
| new_grad_welcome | bool, nullable | null = ad doesn't say |
| deadline | date, nullable | |
| created_at | timestamp | |

**Why versioned rather than overwritten:** when the prompt improves, every ad can be
re-parsed without destroying the previous result, and the two versions can be compared.
Aggregates always read `is_current = true`.

### 4.6 Skill, SkillAlias, AnalysisSkill

| Table | Key fields |
|---|---|
| Skill | `canonical_name` UNIQUE, `category` (language / framework / cloud / database / tool / practice) |
| SkillAlias | `alias` UNIQUE (lower-cased) → skill |
| AnalysisSkill | analysis FK, skill FK **nullable**, `raw_text`, `requirement_level` (`required` \| `nice_to_have`), `evidence` (quoted phrase from the ad) |

`skill` is nullable on purpose: a skill string with no alias match is stored with
`skill = NULL` and its `raw_text`. Unmatched strings are surfaced for review and become
new aliases. The model never decides what the canonical name is.

### 4.7 Application and ApplicationEvent

| Application | Rules |
|---|---|
| role | FK → Role, **UNIQUE** — one application per real-world job |
| via_ad | FK → JobAd — which posting I applied through |
| status | enum; see §8 |
| notes | text |

`ApplicationEvent(application, from_status, to_status, occurred_at)` is append-only.
Current status is on `Application`; history is in the events. Time-in-stage is computed
from events.

### 4.8 CV and CVSkill

One current CV. `CVSkill` mirrors `AnalysisSkill` without `requirement_level`. Gap
analysis is a set comparison between `AnalysisSkill` (for my applications) and
`CVSkill`, weighted by market frequency.

---

## 5. Ingestion and validation order

For every new ad, in this order:

```
1. Validate the input shape (non-empty, size limit, URL format if given)
2. Normalise raw content (whitespace, line endings) and compute content_hash
3. Exact-duplicate checks: content_hash, source_url, (source, external_id)
      → if any match: return the existing JobAd, 200, no new row   (idempotent)
4. Persist JobAd with parse_status = pending, dedup_status = pending
5. Enqueue parse job
6. Return 202 with the JobAd id
```

The request never waits for the LLM.

**Limits:** raw content max 50,000 characters. Over-length content is rejected, not
truncated — the dropped part could be the requirements section.

---

## 6. LLM extraction design

### 6.1 Output contract

The worker requests a JSON object matching a fixed schema: the `JobAdAnalysis` fields
plus a list of `{raw_text, requirement_level, evidence}` skill entries.

- Temperature 0; model and prompt version pinned and recorded.
- Output is validated against the schema before anything is saved.
- On validation failure: retry once. On second failure: `parse_status = failed`, safe
  error recorded, retry endpoint available. Nothing half-valid is ever saved.

### 6.2 Keeping the model out of the parts that need consistency

| Step | Done by |
|---|---|
| Reading the ad, judging required vs nice-to-have | Model |
| Mapping `"Postgres"` / `"psql"` → `PostgreSQL` | **Code** — alias table |
| Mapping `"Klarna AB (publ)"` → Klarna | **Code** — company normalisation (lower-case, strip legal suffixes `AB`, `(publ)`, `Sverige`, then alias lookup) |
| Normalising titles for dedup | **Code** |

### 6.3 Evidence check — a deterministic hallucination guard

Every extracted skill must carry an `evidence` string: the phrase in the ad that
supports it. Before saving, code checks that the evidence appears in the raw content
(case- and whitespace-insensitive).

**An entry whose evidence is not found in the ad is dropped and counted.** This turns
"did the model invent this?" from a judgement into a string comparison, and the
dropped-entry rate becomes a metric (§12).

### 6.4 Drift detection

A golden set of ~20 hand-labelled ads lives in the repo. Any change to prompt, model or
extractor version runs the golden set and reports agreement per field. A change that
lowers agreement doesn't ship.

---

## 7. Deduplication

### 7.1 Exact duplicates — at ingestion (§5)

Same content hash, same URL, or same source + external id → the same `JobAd`. Silent
and idempotent. Enforced by UNIQUE constraints.

### 7.2 Cross-posted roles — after parsing

Runs in the worker once an analysis exists:

```
key = (normalized_company, normalized_title, city)

1. Exact key match to an existing Role     → attach, dedup_status = auto_linked
2. Same company + city, title similarity
   ≥ threshold                              → dedup_status = needs_review
                                              (user confirms merge or keeps separate)
3. No match                                 → create Role, dedup_status = new_role
```

Title normalisation: lower-case, strip seniority words and punctuation, map Swedish ↔
English role terms via a small table (`utvecklare` ↔ `developer`, `backend` ↔
`backend`). The similarity threshold is a tunable constant, chosen by testing on real
ads, not fixed in advance.

**Deliberately not auto-merged on fuzzy match.** A wrong merge silently corrupts
statistics and blocks a legitimate application; a missed merge costs one click. The
asymmetry decides the default.

### 7.3 Rules

| Scenario | Result |
|---|---|
| Same content / URL / external id | ✅ return existing, no new row |
| Exact normalised key match | ✅ auto-link to existing Role |
| Fuzzy title match, same company and city | ⚠️ WARNING — needs review |
| Application to a Role that already has one | ❌ ERROR — 409 |
| Market query over fewer than 30 roles | ❌ refuse with explanation |

---

## 8. Application state machine

```
saved ──▶ applying ──▶ applied ──▶ interviewing ──▶ offer ──▶ accepted
  │           │            │             │            │
  └───────────┴────────────┴─────────────┴────────────┴──▶ rejected / withdrawn
```

- Allowed transitions live in one table in code; anything else returns 409.
- `rejected`, `withdrawn`, `accepted` are terminal.
- Every transition writes an `ApplicationEvent` in the same transaction as the status
  change.
- Date sanity: an `applied` event before the ad's ingestion date → WARNING, confirm to
  continue.

---

## 9. Architecture

```
Browser ──▶ Django REST API ──▶ PostgreSQL
                 │
                 └──▶ Redis ──▶ Celery worker ──▶ LLM API
                                     │
                                     ├── validate + evidence check
                                     ├── normalise skills / company / title
                                     └── dedup → Role

Celery beat ──▶ JobTech adapter (scheduled pull)       [M5]

Frontend polls GET /ads/{id} for parse_status.
```

**Stack:** Python · Django · DRF · PostgreSQL · Redis · Celery · Docker Compose.
Deployment target (M8): AWS (Lambda / SQS / RDS), provisioned with Terraform.

### 9.1 Adapters

Every source implements one interface (`ads/adapters.py`):

```python
class JobAdAdapter(ABC):
    source: str                  # JobAd.source for its ads
    external_id: str | None      # known before fetching? (e.g. read from a link)
    def fetch(self) -> list[RawJobAd]: ...

@dataclass(frozen=True)
class RawJobAd:
    raw_text: str
    source: str                  # "manual_paste" | "jobtech_api"
    source_url: str | None
    source_external_id: str | None
    expires_at: datetime | None  # JobTech's last_publication_date
```

| Adapter | Input | Endpoint |
|---|---|---|
| `PasteAdapter` | text pasted into the form (LinkedIn, company sites, ...) | `POST /api/ads/` |
| `PlatsbankenUrlAdapter` | one Platsbanken link; the ad id is read from the URL | `POST /api/imports/platsbanken/` |
| `JobTechSearchAdapter` | one page (≤100) of a JobTech free-text search | `POST /api/imports/jobtech-search/` |

Both JobTech adapters share `JobTechClient` (HTTP, error mapping, JSON → `RawJobAd`).
Everything downstream sees only `RawJobAd`: `services.ingest()` is the single place a
`JobAd` is created and applies every rule (length, hash, duplicates, warnings). Adding a
source never touches parsing, dedup or aggregation.

**Dropped (2026-10-09):** `FileUploadSource` (saved HTML/PDF). Pasting covers it; the
real need for volume is met by JobTech search import.

**Not scraped, by design:** LinkedIn (terms forbid it, login wall) and company career
sites (every site different, often JS-rendered). Those ads come in by paste.

**Ads disappear.** JobTech returns 404 for a removed ad, the same as for an id that never
existed. So the raw text is stored at import time, `expires_at` records the planned
take-down, and `removed_at` (null = not seen removed) records when it was gone. Removed
ads are kept for statistics.

### 9.2 Layering

Controller → Service → Repository. The pure rules — hashing, normalisation, dedup
matching, state machine, evidence check, aggregation — live in modules with no Django
or HTTP imports, so they are unit-tested directly.

---

## 10. API contract

Base path `/api`. Errors are structured, never a stack trace:

```json
{ "error": "invalid_transition",
  "message": "Cannot move from rejected to interviewing",
  "fields": {} }
```

| Method | Path | Purpose | Success | Errors |
|---|---|---|---|---|
| `POST` | `/ads` | Ingest by paste or upload | **202** new; **200** existing (idempotent) | 400 validation |
| `GET` | `/ads/{id}` | Ad + current analysis + parse/dedup status | 200 | 404 |
| `POST` | `/ads/{id}/retry` | Re-enqueue a failed parse | 202 | 409 if not `failed` |
| `GET` | `/ads?status=&seniority=&city=` | List / filter | 200 | 400 |
| `GET` | `/dedup/review` | Ads with `needs_review` + candidate roles | 200 | |
| `POST` | `/dedup/{ad_id}/resolve` | Merge into a role, or confirm as new | 200 | 409 if not `needs_review` |
| `POST` | `/applications` | Start tracking a role | 201 | 409 already exists |
| `POST` | `/applications/{id}/transition` | Change status | 200 | 409 invalid transition |
| `GET` | `/applications` | Board view | 200 | |
| `GET` | `/market/skills?seniority=&city=&from=&to=` | Skill frequency over roles | 200 | **422** below minimum sample |
| `GET` | `/market/languages?...` | Swedish/English requirement shares | 200 | 422 |
| `POST` | `/cv` | Upload CV, extract skills (async) | 202 | 400 |
| `GET` | `/gap` | Gap analysis | 200 | 422 if no CV or no applications |
| `GET` | `/export/applications.csv` | CSV export | 200 | |

---

## 11. Market aggregation

- Counts are over **Roles**, using each role's most recent current analysis.
- A skill counts once per role, at its strongest requirement level.
- Every response carries `n_roles` and the filter used, so no percentage is shown
  without its denominator.
- Below 30 roles for a filter → 422 with the count, rather than a misleading percentage.
- Statistics describe *the ads collected*, not the Swedish market as a whole. The
  response says so.

---

## 12. Error handling and observability

- LLM timeout, rate limit, malformed output → caught, logged without ad content,
  `failed`, retry available. The worker never crashes.
- No raw ad text, CV text or recruiter details in logs — ids only.

**Metrics (M6, Prometheus):**

| Metric | Why |
|---|---|
| parse success rate | Is extraction working? |
| parse latency p50/p95 | Is the queue keeping up? |
| queue depth | Backlog |
| evidence-dropped entries per ad | Hallucination rate (§6.3) |
| unknown-skill rate | Is the alias table falling behind? |
| dedup `needs_review` rate | Is the threshold sensible? |

---

## 13. Testing strategy

| Layer | What is tested |
|---|---|
| Unit | Content hashing and normalisation; company, skill and title normalisation; dedup key matching and threshold behaviour; every allowed and forbidden state transition; evidence check (present, absent, whitespace differences); schema validation of LLM output; aggregation counts roles not ads; minimum-sample rule |
| Integration | Paste → 202 → worker parses → analysis saved → role created; same ad pasted twice → one row, 200; same role from two sources → auto-linked; fuzzy title → `needs_review`; applying twice to one role → 409; re-parse with a new extractor version keeps the old analysis and flips `is_current` |
| LLM | Always mocked through the client interface in unit and integration tests. The golden set (§6.4) is the only test that calls a real model, run manually. |
| Error paths | Malformed LLM output → retry → `failed`; LLM timeout; over-length ad rejected; invalid transition; market query below minimum |

Tests ship with each milestone, not in one block at the end.

---

## 14. Milestones

| | Deliverable | Course day |
|---|---|---|
| M1 | Paste one ad → store raw → synchronous parse → display. Input validation + hashing tests. | 2 |
| M2 | Full schema with UNIQUE constraints; company/skill/title normalisation with tests. | 3 |
| M3 | Parsing moves to Redis + Celery; status polling; retry endpoint. | 4–6 |
| M4 | Controller / Service / Repository; application state machine; exact dedup; tests. | 7–8 |
| M5 | Adapter interface; file upload + **JobTech** adapter; cross-post dedup + review flow. **The point where this stops being a wrapper.** | 9–10 |
| M6 | Prometheus metrics from §12; Grafana dashboard. | 11 |
| M7 | Market endpoints with minimum-sample rule; CV + gap analysis. | after 11 |
| M8 | AWS deployment, Terraform, DLQ for failed parses. | 12–15 |

---

## 15. Assumptions and open questions

1. **JobTech access terms** — *resolved 2026-10-05:* the JobSearch API
   (`jobsearch.api.jobtechdev.se`) needs no API key. The licence for the ad data still
   needs reading before anything is published from it.
2. **Title similarity threshold** is chosen empirically at M5.
3. **Minimum sample of 30 roles** is a starting point, not a statistical claim.
4. **Evidence check tolerance** — exact substring after normalisation. Paraphrased
   evidence will be dropped; if the drop rate is high, the prompt is fixed rather than
   the check loosened.
5. **The golden set is labelled by the author**, so it encodes her judgement of
   required vs nice-to-have. That's acceptable for a single-user tool and stated here.
6. **Ads are kept after they expire.** They're public data and needed for statistics
   over time. My CV and applications are deleted on request.
