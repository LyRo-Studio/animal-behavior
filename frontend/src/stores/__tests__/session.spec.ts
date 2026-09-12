import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

function mockFetchSequence(responses: Array<{ ok: boolean; json?: () => Promise<unknown> }>) {
  const fn = vi.fn()
  for (const response of responses) {
    fn.mockResolvedValueOnce({
      ok: response.ok,
      json: response.json ?? (() => Promise.resolve({})),
    })
  }
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('session store', () => {
  beforeEach(() => {
    vi.resetModules()
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('login stores tokens and fetches the current account', async () => {
    mockFetchSequence([
      { ok: true, json: () => Promise.resolve({ access_token: 'a1', refresh_token: 'r1' }) },
      {
        ok: true,
        json: () =>
          Promise.resolve({
            id: 1,
            email: 'jan.peeters@vives.be',
            display_name: 'Jan',
            role: 'user',
          }),
      },
    ])

    const { session } = await import('../session')
    await session.login('jan.peeters@vives.be', 'secret')

    expect(session.currentAccount.value?.displayName).toBe('Jan')
    expect(localStorage.getItem('animal-behavior.accessToken')).toBe('a1')
    expect(localStorage.getItem('animal-behavior.refreshToken')).toBe('r1')
  })

  it('login leaves no session behind when it fails', async () => {
    mockFetchSequence([{ ok: false }])

    const { session } = await import('../session')
    await expect(session.login('jan.peeters@vives.be', 'wrong')).rejects.toThrow()

    expect(session.currentAccount.value).toBeNull()
    expect(localStorage.getItem('animal-behavior.accessToken')).toBeNull()
  })

  it('ensureSession returns false when no tokens are stored', async () => {
    const { session } = await import('../session')

    await expect(session.ensureSession()).resolves.toBe(false)
  })

  it('ensureSession rotates a stale access token via the refresh token', async () => {
    localStorage.setItem('animal-behavior.accessToken', 'stale-access-token')
    localStorage.setItem('animal-behavior.refreshToken', 'r1')

    mockFetchSequence([
      { ok: false }, // GET /accounts/me with the stale access token
      { ok: true, json: () => Promise.resolve({ access_token: 'a2', refresh_token: 'r2' }) },
      {
        ok: true,
        json: () =>
          Promise.resolve({
            id: 1,
            email: 'jan.peeters@vives.be',
            display_name: 'Jan',
            role: 'user',
          }),
      },
    ])

    const { session } = await import('../session')
    const result = await session.ensureSession()

    expect(result).toBe(true)
    expect(session.currentAccount.value?.email).toBe('jan.peeters@vives.be')
    expect(localStorage.getItem('animal-behavior.accessToken')).toBe('a2')
  })

  it('ensureSession clears the session when the refresh token is also invalid', async () => {
    localStorage.setItem('animal-behavior.accessToken', 'stale-access-token')
    localStorage.setItem('animal-behavior.refreshToken', 'stale-refresh-token')

    mockFetchSequence([{ ok: false }, { ok: false }])

    const { session } = await import('../session')
    const result = await session.ensureSession()

    expect(result).toBe(false)
    expect(localStorage.getItem('animal-behavior.accessToken')).toBeNull()
  })

  it('logout clears the stored session', async () => {
    localStorage.setItem('animal-behavior.accessToken', 'a1')
    localStorage.setItem('animal-behavior.refreshToken', 'r1')
    mockFetchSequence([{ ok: true }])

    const { session } = await import('../session')
    await session.logout()

    expect(localStorage.getItem('animal-behavior.accessToken')).toBeNull()
    expect(localStorage.getItem('animal-behavior.refreshToken')).toBeNull()
    expect(session.currentAccount.value).toBeNull()
  })
})
