# JobRadar — Requirements Doc (v1)

**Status:** Draft v1 — job-application tracker and Swedish tech job-market analyser,
built on one shared model of job ads
**Last updated:** 2026-09-27

> Working title. Rename freely.

---

## Customer

Students and new graduates in Sweden looking for junior tech roles, LIA
placements or exjobb — starting with the author, who is one.

## Use case

Collect job ads from several sources into one place, turn each ad into structured
data, track the ones I apply to, and use the whole collection to answer a question no
single ad can: **what does the Swedish junior tech market actually ask for?**

## Why this is one product, not two

Both features are built on the same core record — a **job ad**.

- **Market analysis** looks at *every* ad collected.
- **Application tracking** looks at the *subset* of ads I acted on.

An application is not a separate kind of thing; it is a job ad plus my relationship to
it. One `JobAd` table, one `Application` table with a foreign key to it. Two views over
one dataset.

The third feature only exists because the first two share data:

- **Gap analysis** — compare what the ads I applied to require, what my CV contains,
  and how common each requirement is across the market. *"You have applied to 12 roles
  asking for AWS; AWS appears in 38% of junior backend ads; your CV does not mention
  it."* Neither a tracker nor a market report can produce this alone.

## Why this is urgent (for the user)

Job hunting as a student in Sweden means reading dozens of ads in Swedish and English,
across several sites, with no way to see patterns. Decisions about what to learn next
are made on impressions — "everyone seems to want Java" — rather than on data. The same
role appears on several sites under different titles and gets applied to twice or
tracked twice. Application status lives in a spreadsheet, or in memory.

## What people do today

- Browse Platsbanken, LinkedIn, company career pages separately
- Track applications in a spreadsheet, a notes app, or not at all
- Decide what to learn from anecdote and blog posts
- Nothing connects "what I applied to" with "what the market wants"

## Questions this tool must answer

> **TODO (author):** replace these examples with the three questions *you* most want
> answered. The data model is designed around them — if a field doesn't help answer
> one of them, it isn't extracted.

1. *Among junior backend roles in Sweden, what share ask for Java vs Python vs C#?*
2. *What share of junior tech roles list Swedish as a hard requirement?*
3. *Which requirements appear most often in roles I applied to but not on my CV?*

## Inputs required

| Field | Type | Notes |
|---|---|---|
| Job ad source | enum: `manual_paste` \| `file_upload` \| `jobtech_api` | Determines which adapter parses it |
| Raw ad content | text or HTML | Stored as received, never modified — the source of truth for re-parsing |
| Source URL | URL, optional | Used for deduplication when present |
| Source external id | string, optional | e.g. JobTech ad id |
| Application status | enum (see state machine) | Only for ads I act on |
| Application dates | date per status transition | |
| Notes | text | Per application |
| My CV | text or PDF, one current version | Used only for gap analysis |

## What we need the tool to do

### Ingest (priority 1)

- Accept a job ad by pasting text, uploading a saved HTML/PDF, or pulling from the
  JobTech open API.
- Store the **raw content unchanged**, so any ad can be re-parsed later when the
  extraction logic improves.
- Return immediately; parsing happens in the background.

### Parse (priority 1)

- Extract structured fields from each ad: title, company, location, seniority,
  employment type, required skills, nice-to-have skills, language requirements,
  education requirement, whether new graduates are explicitly welcome, application
  deadline.
- Distinguish **required** from **nice-to-have**. This distinction is most of the
  value, and is the part keyword matching cannot do.
- Normalise skills to a canonical list (`"Postgres"`, `"PostgreSQL"`, `"psql"` → one
  skill).
- Mark each ad `pending` → `processing` → `done` / `failed`; allow retry.

### Track applications (priority 2)

- Mark any ad as "applying" and move it through a status pipeline.
- Enforce valid transitions (see state machine).
- Show deadlines approaching for ads not yet applied to.

### Analyse the market (priority 3 — once enough ads exist)

- Aggregate over all parsed ads, filterable by role type, seniority, city, date range.
- Report skill frequency, language-requirement share, new-grad-friendly share.
- Refuse to report on a filter with too few ads to be meaningful, and say why.

### Gap analysis (priority 4)

- Compare required skills across my applications against skills extracted from my CV,
  weighted by how common each skill is in the market.

## Where the LLM is used — and where it is not

Deterministic code wherever possible: reproducible, testable, free, instant. The LLM
only where rules cannot do the job.

| Task | Done by | Why |
|---|---|---|
| Extract fields from free-text ad | **LLM** | Ads are unstructured prose in two languages; required vs nice-to-have is a judgement of language |
| Extract skills from CV | **LLM** | Same reason |
| Normalise skill names to canonical list | **Code** (alias table) | Must be consistent across thousands of ads |
| Deduplicate ads | **Code** | Must be reproducible and testable |
| Application status transitions | **Code** (state machine) | Rules, not language |
| Market aggregation | **Code** | Arithmetic |
| Gap analysis | **Code** | Set comparison over already-extracted data |
| Summary text on the dashboard | Templates first; LLM optional later | Not needed for correctness |

LLM output is validated against a fixed schema before it is saved. An extraction that
fails validation is retried once, then marked `failed` — never saved half-valid.

## Data sources and compliance

- **JobTech / Arbetsförmedlingen open data.** Arbetsförmedlingen states its open data
  and open APIs are free for anyone to use; public APIs do not require partner
  authentication. Using this data does **not** require registering as a job seeker
  with Arbetsförmedlingen — consuming published open data and using the agency's
  job-seeker services are separate things.
  **Open question:** confirm the current access requirements (API key or not) and
  terms for the specific endpoints used before building the adapter.
- **LinkedIn, Indeed and similar sites are not scraped.** Their terms prohibit it. Ads
  from those sites enter only by manual paste or saved-file upload, one at a time,
  by the user.
- **Personal data.** My CV is personal data and stays in my account. Job ads may
  contain recruiter names and contact details; these are not extracted into structured
  fields and are not used in any aggregate.
- **Deletion.** Deleting my account deletes my CV, applications and notes. Collected job
  ads, being public data, may be retained for market statistics.

## Integrity / duplicate detection rules

| Scenario | Handling | Reason |
|---|---|---|
| Same JobTech external id ingested twice | ✅ Accept once, silently | Idempotent import; re-running a batch must not duplicate |
| Same source URL ingested twice | ✅ Accept once, return existing ad | Same ad pasted twice |
| Same company + same normalised title + same city, different source | ⚠️ WARNING — likely duplicate, link as the same role | The same job cross-posted on several sites |
| Same company + similar title (fuzzy match above threshold) + same city | ⚠️ WARNING — confirm to merge or keep separate | `"Backend Developer"` vs `"Backendutvecklare"` vs `"Software Engineer, Backend"` |
| Two applications to the same (deduplicated) role | ❌ ERROR — block | You already applied |
| Invalid status transition (e.g. `rejected` → `interview`) | ❌ ERROR — block | See state machine |
| Application date before the ad was posted | ⚠️ WARNING — confirm to continue | Probably a typo |
| Deadline in the past when marking "applying" | ⚠️ WARNING — confirm to continue | You may be too late |
| Market query on fewer than N ads | ❌ Refuse, explain | Don't report a percentage from 4 data points |

> Company names need their own normalisation (`"Klarna"`, `"Klarna Bank AB"`,
> `"Klarna AB (publ)"`). Deduplication quality depends on it.

> Dates are evaluated on the **Europe/Stockholm** calendar date.

## Application state machine

```
saved ──▶ applying ──▶ applied ──▶ interviewing ──▶ offer ──▶ accepted
  │           │            │             │            │
  └───────────┴────────────┴─────────────┴────────────┴──▶ rejected / withdrawn
```

- `rejected` and `withdrawn` are terminal.
- Every transition is recorded with a timestamp, so time-in-stage can be measured.

## Production-ready requirements

- Every input is validated.
- Ingestion is idempotent; re-importing never duplicates.
- Parsing never blocks a request and never crashes a worker; failures are recoverable
  with one retry.
- Raw ads are immutable; structured fields can always be regenerated from them.
- Errors are safe, clear and contained — no stack traces in responses.
- Controller → Service → Repository layering; dedup, normalisation, state machine and
  aggregation live in modules testable without HTTP.
- Critical logic has automated tests.
- One command runs the whole stack.

## Functional requirements summary

| Feature | Required | Notes |
|---|---|---|
| Ingest by paste / upload | ✅ | MVP |
| Ingest from JobTech API | ✅ | Second adapter |
| Async LLM parsing with retry | ✅ | Core |
| Skill and company normalisation | ✅ | Needed for dedup and analysis |
| Cross-source deduplication | ✅ | The hard problem |
| Application tracking with state machine | ✅ | |
| Market aggregation with minimum-sample rule | ✅ | Needs volume first |
| CV upload + gap analysis | ✅ | Last |
| CSV export | ✅ | Applications and market stats |

## Scope: build order, mapped to the course

| Course day | Built |
|---|---|
| Day 2 | Paste one ad → save raw → show it. Synchronous LLM parse. |
| Day 3 | Tables: `Company`, `JobAd`, `JobAdAnalysis`, `Application`, `Skill` |
| Day 4–6 | Parsing moves to the queue + worker; frontend polls status |
| Day 7 | Controller / Service / Repository split |
| Day 8 | Validation, state machine, duplicate rules, tests |
| Day 9–10 | Adapters: paste, file upload, JobTech API |
| Day 11 | Metrics: parse success rate, queue depth, parse latency |
| Day 12–15 | AWS + Terraform |
| Day 16 | API completeness |
| After | Market dashboards, gap analysis |

**Rule:** no market-analysis UI before the JobTech adapter exists. Without volume,
there is nothing to analyse, and building dashboards early is where scope creep starts.

**Deferred, deliberately:**

| Deferred | Why |
|---|---|
| Browser extension to capture ads | Nice, but a second client; manual paste is enough |
| Email parsing (rejections, interview invites) | Real value, but inbox access is a large privacy surface |
| Salary analysis | Most Swedish ads don't state salary |
| Multi-user / sharing | Single user first |
| Historical trend analysis | Needs months of collected data |

## Assumptions and open questions

1. **One CV version at a time.** Tailored CVs per application are Phase 2.
2. **Skill normalisation uses a hand-maintained alias table**, seeded with common
   variants and extended when unknown skills appear. Fully automatic normalisation is
   not attempted.
3. **Fuzzy title matching threshold** is a tunable number, chosen by testing on real
   ads — not fixed in advance.
4. **Swedish and English ads** are both in scope. Extraction output is always English.
5. **"Junior"** is whatever the ad signals (title, years of experience, "nyexaminerad"),
   extracted by the LLM and stored with the raw evidence.
6. Market statistics describe *the ads collected*, not the whole market. Reports say so.
7. **Web app, not mobile.** Reading ads, comparing requirements and viewing aggregate
   charts are desk tasks.

## Note on scope

This replaces an earlier care-plan generator, abandoned because clinical
decision-support software can fall under the EU Medical Device Regulation, and because
its core was a single LLM call. In this project the LLM does one bounded job —
turning prose into structured fields — and everything built on top of those fields is
deterministic, testable code.
