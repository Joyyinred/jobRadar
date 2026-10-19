// Fails if a CSS class name contains "ad" as a word: ad-list, full-ad, ads...
//
// Ad blockers (EasyList and friends) hide elements with class names like
// these. JobRadar is ALL about job ads, so the name is tempting — and it has
// already made the ad list, and later the full-ad text, vanish for anyone with
// an ad blocker, while looking fine for us. Use "job-" or "posting" instead.
//
// Run by `npm run lint`, so CI catches it.
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// "ad" or "ads" as a whole part of a name, split on - and _ (not "header", "loading").
const BAD = /(^|[-_])ads?($|[-_])/i

function files(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return files(path)
    return /\.(css|tsx)$/.test(entry.name) ? [path] : []
  })
}

const problems = []
for (const file of files('src')) {
  readFileSync(file, 'utf8')
    // Skip /* comments */ (they may name the bad classes, to warn about them),
    // keeping their line breaks so line numbers stay right.
    .replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, ''))
    .split('\n')
    .forEach((line, i) => {
      // .class-name in CSS; className="a b" or className={`a ${x}`} in TSX
      const names = file.endsWith('.css')
        ? [...line.matchAll(/\.([a-zA-Z][\w-]*)/g)].map((m) => m[1])
        : [...line.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\}|\{'([^']*)')/g)]
            .flatMap((m) => (m[1] ?? m[2] ?? m[3]).split(/\s+/))
      for (const name of names) {
        if (BAD.test(name)) problems.push(`${file}:${i + 1}  .${name}`)
      }
    })
}

if (problems.length) {
  console.error('Class names that ad blockers may hide (use "job-" / "posting" instead):')
  for (const p of problems) console.error('  ' + p)
  process.exit(1)
}
