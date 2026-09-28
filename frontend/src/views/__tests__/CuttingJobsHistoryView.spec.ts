import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import CuttingJobsHistoryView from '../CuttingJobsHistoryView.vue'

const listCuttingJobsMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/cuttingJobs', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/cuttingJobs')>()),
  listCuttingJobs: listCuttingJobsMock,
}))

async function mountView() {
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/cutting-jobs', name: 'cutting-jobs-history', component: CuttingJobsHistoryView },
      {
        path: '/cutting-jobs/:id',
        name: 'cutting-job-detail',
        component: { template: '<div>detail</div>' },
      },
      { path: '/cutting', name: 'cutting-upload', component: { template: '<div />' } },
    ],
  })
  router.push('/cutting-jobs')
  await router.isReady()

  const wrapper = mount(CuttingJobsHistoryView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

function output(status: string) {
  return { camera: 'C1', condition: 'ME', phase: 'F1', status, failureReason: null }
}

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: 31,
    testId: 'T001',
    requestedByIdentity: 'jan.peeters@vives.be',
    status: 'succeeded',
    referenceCamera: 'C1',
    createdAt: '2026-09-24T10:00:00Z',
    startedAt: '2026-09-24T10:01:00Z',
    finishedAt: '2026-09-24T10:05:00Z',
    sourceRetained: false,
    outputs: [output('succeeded'), output('succeeded')],
    ...overrides,
  }
}

describe('CuttingJobsHistoryView', () => {
  afterEach(() => {
    listCuttingJobsMock.mockReset()
  })

  it('lists every cutting job with its Test, status, Cuts done and who ran it', async () => {
    listCuttingJobsMock.mockResolvedValue([
      job({
        id: 32,
        testId: 'T002',
        status: 'running',
        outputs: [output('succeeded'), output('pending')],
      }),
      job({ id: 31, testId: 'T001', requestedByIdentity: null }),
    ])

    const { wrapper } = await mountView()

    const rows = wrapper.findAll('[data-testid="cutting-history-row"]')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('T002')
    expect(rows[0]!.find('[data-testid="cutting-history-status"]').text()).toContain('Running')
    expect(rows[0]!.text()).toContain('1/2 Cuts')
    expect(rows[0]!.find('[data-testid="cutting-history-requested-by"]').text()).toContain(
      'jan.peeters@vives.be',
    )
    expect(rows[1]!.text()).toContain('T001')
    expect(rows[1]!.find('[data-testid="cutting-history-requested-by"]').exists()).toBe(false)
  })

  it('links each job to its progress view', async () => {
    listCuttingJobsMock.mockResolvedValue([job({ id: 31 })])

    const { wrapper, router } = await mountView()
    await wrapper.find('[data-testid="cutting-history-link"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/cutting-jobs/31')
  })

  it('says so when there are no cutting jobs yet', async () => {
    listCuttingJobsMock.mockResolvedValue([])

    const { wrapper } = await mountView()

    expect(wrapper.text()).toContain('No cutting jobs yet.')
  })

  it('shows why the history could not be loaded', async () => {
    listCuttingJobsMock.mockRejectedValue(new Error('Database unavailable.'))

    const { wrapper } = await mountView()

    expect(wrapper.find('[role="alert"]').text()).toBe('Database unavailable.')
  })
})
