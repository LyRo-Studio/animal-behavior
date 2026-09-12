import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import StatusView from '../StatusView.vue'

function mockFetchOnce(response: { ok: boolean; status?: number; json?: () => Promise<unknown> }) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: response.ok,
      status: response.status ?? 200,
      json: response.json ?? (() => Promise.resolve({})),
    }),
  )
}

describe('StatusView', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders the backend status once the health check resolves', async () => {
    mockFetchOnce({ ok: true, json: () => Promise.resolve({ status: 'ok' }) })

    const wrapper = mount(StatusView)
    await flushPromises()

    expect(wrapper.text()).toContain('ok')
    wrapper.unmount()
  })

  it('renders an unreachable state when the health check fails', async () => {
    mockFetchOnce({ ok: false, status: 503 })

    const wrapper = mount(StatusView)
    await flushPromises()

    expect(wrapper.text().toLowerCase()).toContain('unreachable')
    // Unmount before the scheduled retry can fire and touch a detached
    // component once this test has already moved on.
    wrapper.unmount()
  })
})
