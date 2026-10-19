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

export const SWEDISH_LABELS: Record<string, string> = {
  required: 'Swedish required',
  preferred: 'Swedish a plus',
  not_mentioned: 'Swedish not mentioned',
}

export const label = (labels: Record<string, string>, code: string) => labels[code] ?? code

export const percent = (share: number) => `${(share * 100).toFixed(1)}%`
