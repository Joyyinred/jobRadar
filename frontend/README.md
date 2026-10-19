# JobRadar frontend

React + TypeScript, built with Vite. Talks only to the Django API (`/api/...`).

## Run it

With the rest of the stack (recommended):

```bash
docker compose up
```

Then open http://localhost:5173. Vite forwards `/api/...` to the `web` service,
so there's no CORS to configure. Edits reload in the browser instantly.

On your own machine, with Django on http://localhost:8000:

```bash
npm install
npm run dev
```

## Files

| File | What it does |
|---|---|
| `src/api.ts` | Every API call and the TypeScript type of every response. The only place that calls `fetch()`. |
| `src/App.tsx` | Page layout and the state panels share (selected ad, list refresh, occupation groups). |
| `src/components/` | One component per panel: adding ads, search import, ad list, ad detail, market. |
| `src/labels.ts` | API codes (`fixed_term`) to readable text (`Fixed term`). |
| `src/index.css` | Design tokens (colours, radii), light and dark. |

## Checks (also run in CI)

```bash
npm run lint    # oxlint, incl. React hook rules
npm run build   # TypeScript type check + production build into dist/
```
