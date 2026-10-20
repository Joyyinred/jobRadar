// The API speaks in codes ("fixed_term"); people read labels ("Fixed term").

export const SENIORITY_LABELS: Record<string, string> = {
  intern: 'Intern',
  junior: 'Junior',
  mid: 'Mid',
  senior: 'Senior',
  unspecified: 'Level not stated',
}

export const EMPLOYMENT_LABELS: Record<string, string> = {
  permanent: 'Permanent',
  fixed_term: 'Fixed term',
  internship: 'Internship',
  thesis: 'Thesis work',
  unspecified: 'Type not stated',
}

// The level the ad names: for a thesis, which thesis; for a job, which degree.
export const DEGREE_LABELS: Record<string, string> = {
  master: "Master's",
  bachelor: "Bachelor's",
  either: "Bachelor's or master's",
  unspecified: 'Level not stated',
}

// Your application's status, in the order an application usually goes.
export const STATUS_LABELS: Record<string, string> = {
  saved: 'Saved',
  applied: 'Applied',
  interview: 'Interview',
  offer: 'Offer',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
}

export const SWEDISH_LABELS: Record<string, string> = {
  required: 'Swedish required',
  preferred: 'Swedish a plus',
  not_mentioned: 'Swedish not mentioned',
}

export const ORIGIN_LABELS: Record<string, string> = {
  user: 'Added by me',
  import: 'Imported',
}

export const label = (labels: Record<string, string>, code: string) => labels[code] ?? code

export const percent = (share: number) => `${(share * 100).toFixed(1)}%`
