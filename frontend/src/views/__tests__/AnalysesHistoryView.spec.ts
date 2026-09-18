import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import AnalysesHistoryView from '../AnalysesHistoryView.vue'

const listAnalysesMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/analyses', () => ({
  listAnalyses: listAnalysesMock,
}))

vi.mock('@/stores/session', () => ({
  session: { accessToken: { value: 'a-token' } },
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/analyses', name: 'analyses-history', component: AnalysesHistoryView },
      {
        path: '/analyses/:id',
        name: 'analysis-detail',
        component: { template: '<div>detail</div>' },
      },
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
    ],
  })
}

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: 42,
    testId: 'T001',
    status: 'completed',
    dogtraceVersion: '1.1.1',
    reportAvailable: true,
    createdAt: '2026-01-02T10:00:00Z',
    startedAt: '2026-01-02T10:00:05Z',
    finishedAt: '2026-01-02T10:05:00Z',
    videos: [],
    ...overrides,
  }
}

async function mountView() {
  const router = createTestRouter()
  router.push('/analyses')
  await router.isReady()

  const wrapper = mount(AnalysesHistoryView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

describe('AnalysesHistoryView', () => {
  afterEach(() => {
    listAnalysesMock.mockReset()
  })

  it("loads and lists the current Account's own analyses with status and date", async () => {
    listAnalysesMock.mockResolvedValue([
      job({ id: 42, testId: 'T001', status: 'completed' }),
      job({ id: 41, testId: 'T002', status: 'failed' }),
    ])

    const wrapper = await mountView()

    expect(listAnalysesMock).toHaveBeenCalledWith('a-token')
    expect(wrapper.text()).toContain('T001')
    expect(wrapper.text()).toContain('completed')
    expect(wrapper.text()).toContain('T002')
    expect(wrapper.text()).toContain('failed')
  })

  it("links each row to that job's detail page", async () => {
    listAnalysesMock.mockResolvedValue([job({ id: 42 })])

    const wrapper = await mountView()

    const link = wrapper.find('[data-testid="analysis-history-link"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('/analyses/42')
  })

  it('shows an empty state when the Account has no analyses yet', async () => {
    listAnalysesMock.mockResolvedValue([])

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('No analyses yet.')
  })

  it('shows an error message when loading fails', async () => {
    listAnalysesMock.mockRejectedValue(new Error('boom'))

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('Failed to load analyses.')
  })
})
