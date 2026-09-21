// `||`, not `??`: an explicitly empty VITE_API_BASE_URL (e.g. an
// unpopulated build var in some CI/preview config) should also fall back
// to the default, not resolve to a same-origin relative request.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

// Shared by services whose backend endpoints return a safe, user-facing
// `detail` string on failure (Analyses, ...) — there's no reason to hide
// these; the backend already picked a message safe to show as-is.
export async function errorFromResponse(response: Response, fallback: string): Promise<Error> {
  const body = await response.json().catch(() => null)
  return new Error(typeof body?.detail === 'string' ? body.detail : fallback)
}
