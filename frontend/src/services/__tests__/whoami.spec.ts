import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchIdentity } from '../whoami'

describe('fetchIdentity', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns the identity the backend echoes back', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ identity: 'jan.peeters@vives.be' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchIdentity()).resolves.toBe('jan.peeters@vives.be')
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/whoami$/)
  })

  it('returns null when no identity was forwarded', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ identity: null }) }),
    )

    await expect(fetchIdentity()).resolves.toBeNull()
  })

  it('sends no credentials or Authorization header', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ identity: null }) })
    vi.stubGlobal('fetch', fetchMock)

    await fetchIdentity()

    expect(fetchMock.mock.calls[0][1]).toBeUndefined()
  })

  it('throws when the backend answers with an error status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }))

    await expect(fetchIdentity()).rejects.toThrow('500')
  })
})
