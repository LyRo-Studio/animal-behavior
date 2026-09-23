import { describe, expect, it } from 'vitest'

import router from '../index'

// Ticket #72: the app has no login of its own, so there is no navigation
// guard to test — what's worth pinning down is that every page is reachable
// straight away and that the removed auth/admin routes stay gone.
describe('router', () => {
  it.each([
    ['/', 'home'],
    ['/status', 'status'],
    ['/media', 'media'],
    ['/analyses', 'analyses-history'],
    ['/analyses/7', 'analysis-detail'],
    ['/consolidation', 'consolidation'],
    ['/consolidations', 'consolidations-history'],
  ])('lets a visitor straight onto %s', async (path, name) => {
    await router.push(path)
    await router.isReady()

    expect(router.currentRoute.value.name).toBe(name)
  })

  it('has no login, password or admin routes', () => {
    const names = router.getRoutes().map((route) => route.name)

    for (const removed of ['login', 'forgot-password', 'set-password', 'admin']) {
      expect(names).not.toContain(removed)
    }
    expect(router.resolve('/login').matched).toHaveLength(0)
    expect(router.resolve('/admin').matched).toHaveLength(0)
  })
})
