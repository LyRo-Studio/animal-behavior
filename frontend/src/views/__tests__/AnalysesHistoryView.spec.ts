import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import AnalysesHistoryView from '../AnalysesHistoryView.vue'

const listAnalysesMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/analyses', () => ({
  listAnalyses: listAnalysesMock,
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
    testIds: ['T001'],
    requestedByIdentity: 'jan.peeters@vives.be',
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

function video(overrides: Record<string, unknown> = {}) {
  return {
    cutKey: 'cuts/T001/T001_C2_ME_F1.mp4',
    position: 0,
    status: 'succeeded',
    failureReason: null,
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

  it('loads and lists every analysis with status and date', async () => {
    listAnalysesMock.mockResolvedValue([
      job({ id: 42, testIds: ['T001'], status: 'completed' }),
      job({ id: 41, testIds: ['T002'], status: 'failed' }),
    ])

    const wrapper = await mountView()

    expect(listAnalysesMock).toHaveBeenCalledWith()
    expect(wrapper.text()).toContain('T001')
    expect(wrapper.text()).toContain('completed')
    expect(wrapper.text()).toContain('T002')
    expect(wrapper.text()).toContain('failed')
  })

  it('shows who ran each analysis, and leaves it out when unattributed', async () => {
    listAnalysesMock.mockResolvedValue([
      job({ id: 42, requestedByIdentity: 'jan.peeters@vives.be' }),
      job({ id: 41, requestedByIdentity: null }),
    ])

    const wrapper = await mountView()

    const runBy = wrapper.findAll('[data-testid="analysis-history-requested-by"]')
    expect(runBy).toHaveLength(1)
    expect(runBy[0].text()).toContain('jan.peeters@vives.be')
  })

  it("links each row to that job's detail page", async () => {
    listAnalysesMock.mockResolvedValue([job({ id: 42 })])

    const wrapper = await mountView()

    const link = wrapper.find('[data-testid="analysis-history-link"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('/analyses/42')
  })

  it('shows an empty state when there are no analyses yet', async () => {
    listAnalysesMock.mockResolvedValue([])

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('No analyses yet.')
  })

  it('shows an error message when loading fails', async () => {
    listAnalysesMock.mockRejectedValue(new Error('boom'))

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('Failed to load analyses.')
  })

  // Issue #92: "T041: 8/8, T002: 6/7" — succeeded/total per Test, only for
  // a job spanning more than one Test.
  it('shows succeeded/total per Test for a multi-Test job only', async () => {
    listAnalysesMock.mockResolvedValue([
      job({
        id: 42,
        testIds: ['T041', 'T002'],
        status: 'completed_with_errors',
        videos: [
          video({ cutKey: 'cuts/T041/T041_C2_ME_F1.mp4', position: 0 }),
          video({ cutKey: 'cuts/T041/T041_C2_ME_F2.mp4', position: 1 }),
          video({ cutKey: 'cuts/T002/T002_C2_ME_F1.mp4', position: 2 }),
          video({
            cutKey: 'cuts/T002/T002_C2_ME_F2.mp4',
            position: 3,
            status: 'failed',
            failureReason: 'The analysis did not produce a result for this video.',
          }),
        ],
      }),
      job({
        id: 41,
        testIds: ['T001'],
        videos: [video()],
      }),
    ])

    const wrapper = await mountView()

    const breakdowns = wrapper.findAll('[data-testid="analysis-history-breakdown"]')
    expect(breakdowns.map((b) => b.text())).toEqual(['T041: 2/2, T002: 1/2'])
  })
})
