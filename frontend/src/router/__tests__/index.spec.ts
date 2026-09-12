import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const ensureSessionMock = vi.hoisted(() => vi.fn())

vi.mock('@/stores/session', () => ({
  session: { ensureSession: ensureSessionMock, currentAccount: { value: null } },
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

  it('never checks for a session on the public set-password route', async () => {
    const { default: router } = await import('../index')
    await router.push({ name: 'set-password', query: { token: 'abc' } })
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('set-password')
    expect(ensureSessionMock).not.toHaveBeenCalled()
  })

  it('requires a session for the status route too — only Login and set-password are exempt', async () => {
    ensureSessionMock.mockResolvedValue(false)

    const { default: router } = await import('../index')
    await router.push('/status')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('login')
    expect(ensureSessionMock).toHaveBeenCalled()
  })

  it('redirects a non-Admin away from the Admin route, to Home', async () => {
    ensureSessionMock.mockResolvedValue(true)

    const { session } = await import('@/stores/session')
    session.currentAccount.value = {
      id: 1,
      email: 'jan.peeters@vives.be',
      displayName: 'Jan',
      role: 'user',
    }

    const { default: router } = await import('../index')
    await router.push('/admin')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('allows an Admin to reach the Admin route', async () => {
    ensureSessionMock.mockResolvedValue(true)

    const { session } = await import('@/stores/session')
    session.currentAccount.value = {
      id: 2,
      email: 'admin.person@vives.be',
      displayName: 'Admin',
      role: 'admin',
    }

    const { default: router } = await import('../index')
    await router.push('/admin')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('admin')
  })
})
