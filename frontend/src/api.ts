// Everything the frontend knows about the Django API, in one place:
// the shape of each response (types) and one function per endpoint.
// Components never call fetch() themselves — they call these.

// ---- Types: what the API sends back ------------------------------------------

export type ParseStatus = 'pending' | 'processing' | 'completed' | 'failed'
export type Seniority = 'intern' | 'junior' | 'mid' | 'senior' | 'unspecified'
export type EmploymentType = 'permanent' | 'fixed_term' | 'internship' | 'thesis' | 'unspecified'

/** One row of GET /api/ads/ */
export type AdSummary = {
  id: number
  status: ParseStatus
  title: string | null // as the ad wrote it, often Swedish
  title_en: string | null // in English
  company: string | null
  seniority: string // "" until parsed
  employment_type: string
  degree_level: string // bachelor | master | either | unspecified ("" until parsed)
  occupation_group: string // "" for pasted ads
  origin: Origin
  closed: boolean // taken down, or past its deadline
}

/** Who put an ad in: you (Add ads page) or a bulk import. */
export type Origin = 'user' | 'import'

/** GET /api/ads/ — one page of the list. */
export type AdPage = {
  count: number // ads matching the filters, on all pages
  page: number
  pages: number
  page_size: number
  results: AdSummary[]
}

/** The parsed fields of a completed ad. */
export type AdResult = {
  title: string
  title_en: string
  company: string | null
  city: string
  seniority: string
  employment_type: string
  degree_level: string
  occupation_group: string
  required_skills: string[] // each required on its own
  required_one_of: string[][] // [["Python", "Java", "Go"]]: any one of each group
  nice_to_have_skills: string[]
  nice_to_have_one_of: string[][]
  swedish_requirement: string
}

/** GET /api/ads/<id>/ — plus `duplicate` on a 200 from POST. */
export type AdDetail = {
  id: number
  status: ParseStatus
  result?: AdResult // only when completed
  error?: string // only when failed
  listing?: Listing // only from GET /api/ads/<id>/
  duplicate?: boolean
}

/** The ad as it was posted: full text and how to apply. */
export type Listing = {
  text: string
  ad_url: string | null // the ad's page (Platsbanken), or the link given when pasting
  apply_url: string | null // the employer's application form, if stated
  apply_email: string | null
  apply_by: string | null // ISO date-time: the application deadline
  removed_at: string | null // seen taken down at the source
}

export type OccupationGroup = { code: string; label: string; ads: number }

export type ImportJob = {
  id: number
  status: 'pending' | 'running' | 'completed' | 'failed'
  params: { query: string; limit: number; data_it_only: boolean }
  fetched: number
  created: number
  duplicates: number
  skipped: number
  error: string
}

/** required + one_of + nice_to_have = ads */
export type SkillStat = {
  name: string
  ads: number
  share: number
  required: number // asked for on its own
  one_of: number // required, but as one of several alternatives
  nice_to_have: number
}

export type MarketSkills = {
  sample_size: number
  skills: SkillStat[]
  swedish_requirement: { required: number; preferred: number; not_mentioned: number }
  note: string
}

// ---- Errors: the one error format the backend always uses (Day 8) -------------

export type ErrorType = 'validation' | 'not_found' | 'block' | 'warning' | 'insufficient_data' | 'upstream' | 'error'

/** Thrown for every non-2xx answer; carries the backend's {type, code, message, detail}. */
export class ApiError extends Error {
  type: ErrorType
  code: string
  detail: Record<string, unknown>
  status: number

  constructor(status: number, body: { type?: ErrorType; code?: string; message?: string; detail?: Record<string, unknown> }) {
    super(body.message ?? `Request failed (${status})`)
    this.status = status
    this.type = body.type ?? 'error'
    this.code = body.code ?? 'UNKNOWN'
    this.detail = body.detail ?? {}
  }
}

// ---- The one place that calls fetch() ------------------------------------------

/** Django's csrftoken cookie. Sent back as a header on every write: another
 *  site can make the browser send our cookies, but can't READ them — so it
 *  can't send this header. That's the whole CSRF defence. (The session cookie
 *  itself is HttpOnly: this code can't see it, and doesn't need to.) */
function csrfToken(): string {
  return document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] ?? ''
}

async function request<T>(url: string, init?: RequestInit): Promise<{ data: T; status: number }> {
  const writes = (init?.method ?? 'GET') !== 'GET'
  let response: Response
  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        // Read fresh each time: Django issues a new token when you log in.
        ...(writes ? { 'X-CSRFToken': csrfToken() } : {}),
        ...init?.headers,
      },
    })
  } catch {
    // Network down, server restarting...
    throw new ApiError(0, { type: 'error', code: 'NETWORK', message: 'Could not reach the server. Please try again.' })
  }
  // 204 No Content (e.g. DELETE): success with no body to read.
  if (response.status === 204) return { data: undefined as T, status: 204 }
  let body: unknown
  try {
    body = await response.json()
  } catch {
    // Not JSON — e.g. a proxy's HTML error page.
    throw new ApiError(response.status, { type: 'error', code: 'BAD_RESPONSE', message: 'The server sent an unexpected answer.' })
  }
  if (!response.ok) throw new ApiError(response.status, body as object)
  return { data: body as T, status: response.status }
}

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

/** ?seniority=junior&seniority=intern&... — repeated keys for lists, empty values left out. */
function query(params: Record<string, string | string[] | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    for (const v of Array.isArray(value) ? value : [value]) {
      if (v !== undefined && v !== '') search.append(key, String(v))
    }
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

// ---- One function per endpoint -------------------------------------------------

export type AdFilters = {
  seniority?: string[]
  employment_type?: string
  occupation_group?: string
  origin?: string
  degree_level?: string // "master" also matches "either", same for "bachelor"
  page?: number
}

export const listAds = async (filters: AdFilters) => (await request<AdPage>(`/api/ads/${query(filters)}`)).data

export const getAd = async (id: number) => (await request<AdDetail>(`/api/ads/${id}/`)).data

export const retryAd = async (id: number) => (await request<AdDetail>(`/api/ads/${id}/retry/`, post({}))).data

/** POST /api/ads/ — 202 = new ad queued, 200 = duplicate (the ad we already have). */
export const submitAd = async (body: { raw_text: string; source_url: string; confirm: boolean }) =>
  (await request<AdDetail>('/api/ads/', post(body))).data

export const importPlatsbankenLink = async (body: { url: string; confirm: boolean }) =>
  (await request<AdDetail>('/api/imports/platsbanken/', post(body))).data

export const startSearchImport = async (body: { query: string; limit: number; data_it_only: boolean }) =>
  (await request<ImportJob>('/api/imports/jobtech-search/', post(body))).data

export const getImport = async (id: number) => (await request<ImportJob>(`/api/imports/${id}/`)).data

export const getOccupationGroups = async () => (await request<OccupationGroup[]>('/api/occupation-groups/')).data

export type MarketFilters = { seniority?: string[]; level?: string; occupation_group?: string; top?: number }

// ---- Your applications (logged in) ------------------------------------------------

export type ApplicationStatus = 'saved' | 'applied' | 'interview' | 'offer' | 'rejected' | 'withdrawn'

export type Application = {
  ad: { id: number; title: string | null; company: string | null; apply_by: string | null; closed: boolean }
  status: ApplicationStatus
  notes: string
  created_at: string
  updated_at: string
  applied_at: string | null
  events: { status: ApplicationStatus; at: string }[] // the history, oldest first
}

export type ApplicationSummary = {
  counts: Record<ApplicationStatus, number>
  applied: number // got past "saved"
  heard_back: number // of those, got an interview / offer / rejection
  days_to_answer: number | null // median days from applying to the first answer
}

export const getApplications = async () =>
  (await request<{ results: Application[]; summary: ApplicationSummary }>('/api/applications/')).data

/** Your application for this ad, or null if you're not tracking it. */
export const getAdApplication = async (adId: number) =>
  (await request<{ application: Application | null }>(`/api/ads/${adId}/application/`)).data.application

/** Start tracking, or change status / notes. Leave a field out to keep it. */
export const trackAd = async (adId: number, body: { status?: ApplicationStatus; notes?: string }) =>
  (await request<{ application: Application }>(`/api/ads/${adId}/application/`, { method: 'PUT', body: JSON.stringify(body) }))
    .data.application

export const untrackAd = async (adId: number) => {
  await request<void>(`/api/ads/${adId}/application/`, { method: 'DELETE' })
}

// ---- Logging in ----------------------------------------------------------------

export type User = { username: string }
type Me = { user: User | null }

/** Who's logged in (null = nobody). Call it first: it also sets the csrftoken cookie. */
export const getMe = async () => (await request<Me>('/api/auth/me/')).data.user

export const logIn = async (username: string, password: string) =>
  (await request<Me>('/api/auth/login/', post({ username, password }))).data.user

export const logOut = async () => {
  await request<Me>('/api/auth/logout/', post({}))
}

export const getMarketSkills = async (filters: MarketFilters) =>
  (await request<MarketSkills>(`/api/market/skills/${query(filters)}`)).data
