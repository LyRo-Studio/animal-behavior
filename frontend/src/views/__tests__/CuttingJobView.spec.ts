import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import CuttingJobView from '../CuttingJobView.vue'

const getCuttingJobMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/cuttingJobs', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/cuttingJobs')>()),
  getCuttingJob: getCuttingJobMock,
}))

async function mountView(id = '31') {
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/cutting-jobs/:id', name: 'cutting-job-detail', component: CuttingJobView },
      { path: '/cutting', name: 'cutting-upload', component: { template: '<div />' } },
    ],
  })
  router.push(`/cutting-jobs/${id}`)
  await router.isReady()

  const wrapper = mount(CuttingJobView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

function output(status: string) {
  return { camera: 'C1', condition: 'ME', phase: 'F1', status, failureReason: null }
}

describe('CuttingJobView', () => {
  afterEach(() => {
    getCuttingJobMock.mockReset()
  })

  it("shows the job's Test, status, and how many of its Cuts are done", async () => {
    getCuttingJobMock.mockResolvedValue({
      id: 31,
      testId: 'T001',
      requestedByIdentity: 'researcher@example.com',
      status: 'running',
      referenceCamera: 'C1',
      createdAt: '2026-09-24T10:00:00Z',
      startedAt: '2026-09-24T10:01:00Z',
      finishedAt: null,
      outputs: [output('succeeded'), output('pending'), output('pending')],
    })

    const wrapper = await mountView('31')

    expect(getCuttingJobMock).toHaveBeenCalledWith(31)
    expect(wrapper.find('h1').text()).toContain('Cutting job #31')
    expect(wrapper.text()).toContain('T001')
    expect(wrapper.find('[data-testid="job-status"]').text()).toBe('Running')
    expect(wrapper.find('[data-testid="cuts-done"]').text()).toContain('1 of 3')
  })

  it('shows why the job could not be loaded', async () => {
    getCuttingJobMock.mockRejectedValue(new Error('Cutting job not found.'))

    const wrapper = await mountView('999')

    expect(wrapper.find('[role="alert"]').text()).toBe('Cutting job not found.')
  })
})
