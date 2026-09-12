import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const ensureSessionMock = vi.hoisted(() => vi.fn())

vi.mock('@/stores/session', () => ({
  session: { ensureSession: ensureSessionMock },
}))

describe('router navigation guard', () => {
  beforeEach(() => {
    vi.resetModules()
    ensureSessionMock.mockReset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('redirects an unauthenticated visitor to Login, preserving the intended destination', async () => {
    ensureSessionMock.mockResolvedValue(false)

    const { default: router } = await import('../index')
    await router.push('/')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/')
  })

  it('allows navigation to a protected route once a session is established', async () => {
    ensureSessionMock.mockResolvedValue(true)

    const { default: router } = await import('../index')
    await router.push('/')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('never checks for a session on the public Login route', async () => {
    const { default: router } = await import('../index')
    await router.push('/login')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('login')
    expect(ensureSessionMock).not.toHaveBeenCalled()
  })

  it('requires a session for the status route too — only Login is exempt', async () => {
    ensureSessionMock.mockResolvedValue(false)

    const { default: router } = await import('../index')
    await router.push('/status')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('login')
    expect(ensureSessionMock).toHaveBeenCalled()
  })
})
