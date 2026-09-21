// Where the backend's `/api` lives. Production builds bake in `/api` — a
// same-origin path that the reverse proxy in front of the app (nginx/,
// ticket #71) routes to the backend, so the browser never talks to a second
// origin (an `http://` one would be blocked as mixed content on the site's
// `https://` origin anyway). Local development talks to the backend
// directly, hence the absolute default.
//
// `||`, not `??`: an explicitly empty VITE_API_BASE_URL (e.g. an
// unpopulated build var in some CI/preview config) should also fall back
// to the default, not resolve to a same-origin relative request.
//
// May be relative (`/api`), so `new URL(`${API_BASE_URL}...`)` throws — build
// any URL that needs query parameters with `apiUrl()` below.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api'

// A full `URL` for a backend path, for callers that need to add query
// parameters. `new URL(API_BASE_URL + path)` alone throws for a relative
// base like `/api`, so resolve it against the page's own origin.
export function apiUrl(path: string): URL {
  return new URL(`${API_BASE_URL}${path}`, window.location.origin)
}

// Shared by services whose backend endpoints return a safe, user-facing
// `detail` string on failure (Analyses, ...) — there's no reason to hide
// these; the backend already picked a message safe to show as-is.
export async function errorFromResponse(response: Response, fallback: string): Promise<Error> {
  const body = await response.json().catch(() => null)
  return new Error(typeof body?.detail === 'string' ? body.detail : fallback)
}
