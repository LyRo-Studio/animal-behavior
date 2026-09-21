import { afterEach, describe, expect, it, vi } from 'vitest'

// `API_BASE_URL` is read from `import.meta.env` once, when the module is
// first evaluated — so each case sets the env, resets the module registry
// and imports fresh.
async function loadWithBase(base: string) {
  vi.stubEnv('VITE_API_BASE_URL', base)
  vi.resetModules()
  return {
    apiBase: await import('../apiBase'),
    mediaBrowser: await import('../mediaBrowser'),
  }
}

describe('with the production same-origin base (/api)', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('builds URLs on the page origin instead of throwing on a relative base', async () => {
    const { apiBase } = await loadWithBase('/api')

    expect(apiBase.apiUrl('/media/datasets').toString()).toBe(
      `${window.location.origin}/api/media/datasets`,
    )
  })

  it('points the <video src> stream link at the same origin, token as a query parameter', async () => {
    const { mediaBrowser } = await loadWithBase('/api')

    const url = new URL(mediaBrowser.mediaStreamUrl('cuts/T001/a.mp4', 'play', 'tok'))

    expect(url.origin).toBe(window.location.origin)
    expect(url.pathname).toBe('/api/media/stream')
    expect(url.searchParams.get('key')).toBe('cuts/T001/a.mp4')
    expect(url.searchParams.get('action')).toBe('play')
    expect(url.searchParams.get('token')).toBe('tok')
  })
})

describe('with an absolute base (local development)', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('keeps talking to that origin directly', async () => {
    const { apiBase } = await loadWithBase('http://localhost:8000/api')

    expect(apiBase.apiUrl('/analyses').toString()).toBe('http://localhost:8000/api/analyses')
  })

  it('falls back to the local backend when the variable is empty', async () => {
    const { apiBase } = await loadWithBase('')

    expect(apiBase.API_BASE_URL).toBe('http://localhost:8000/api')
  })
})
