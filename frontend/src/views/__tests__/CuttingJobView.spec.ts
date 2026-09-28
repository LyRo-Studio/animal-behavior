import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import CuttingJobView from '../CuttingJobView.vue'

const getCuttingJobMock = vi.hoisted(() => vi.fn())
const cancelCuttingJobMock = vi.hoisted(() => vi.fn())
const discardCuttingJobSourceMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/cuttingJobs', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/cuttingJobs')>()),
  getCuttingJob: getCuttingJobMock,
  cancelCuttingJob: cancelCuttingJobMock,
  discardCuttingJobSource: discardCuttingJobSourceMock,
}))

// The real class, so the view's `instanceof` check sees the same constructor.
const { CuttingJobNotFoundError } = await import('@/services/cuttingJobs')

async function mountView(id = '31') {
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/cutting-jobs/:id', name: 'cutting-job-detail', component: CuttingJobView },
      { path: '/cutting', name: 'cutting-upload', component: { template: '<div />' } },
      {
        path: '/cutting-jobs',
        name: 'cutting-jobs-history',
        component: { template: '<div />' },
      },
    ],
  })
  router.push(`/cutting-jobs/${id}`)
  await router.isReady()

  const wrapper = mount(CuttingJobView, { global: { plugins: [router] } })
  await flushPromises()
  return Object.assign(wrapper, { router })
}

function output(
  condition: string,
  phase: string,
  status = 'pending',
  overrides: Record<string, unknown> = {},
) {
  return { camera: 'C1', condition, phase, status, failureReason: null, ...overrides }
}

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: 31,
    testId: 'T001',
    requestedByIdentity: 'researcher@example.com',
    status: 'running',
    referenceCamera: 'C1',
    createdAt: '2026-09-24T10:00:00Z',
    startedAt: '2026-09-24T10:01:00Z',
    finishedAt: null,
    sourceRetained: true,
    outputs: [output('ME', 'F1', 'succeeded'), output('ME', 'F2'), output('ME', 'F3')],
    ...overrides,
  }
}

function cutsDone(wrapper: VueWrapper) {
  return wrapper.find('[data-testid="cuts-done"]').text()
}

// Each phase row's cells, as "<phase>: <status per camera>".
function phaseRows(wrapper: VueWrapper) {
  return wrapper
    .findAll('[data-testid="phase-row"]')
    .map((row) =>
      [row.find('th').text(), ...row.findAll('td').map((cell) => cell.text())].join(' | '),
    )
}

describe('CuttingJobView', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    getCuttingJobMock.mockReset()
    cancelCuttingJobMock.mockReset()
    discardCuttingJobSourceMock.mockReset()
  })

  it("shows the job's Test, status, requester, and how many of its Cuts are done", async () => {
    getCuttingJobMock.mockResolvedValue(job())

    const wrapper = await mountView('31')

    expect(getCuttingJobMock).toHaveBeenCalledWith(31)
    expect(wrapper.find('h1').text()).toContain('Cutting job #31')
    expect(wrapper.text()).toContain('T001')
    expect(wrapper.text()).toContain('researcher@example.com')
    expect(wrapper.find('[data-testid="job-status"]').text()).toBe('Running')
    expect(cutsDone(wrapper)).toContain('1 of 3')
  })

  it('shows live per-phase progress while the job runs, without a manual refresh', async () => {
    getCuttingJobMock.mockResolvedValueOnce(job()).mockResolvedValueOnce(
      job({
        outputs: [
          output('ME', 'F1', 'succeeded'),
          output('ME', 'F2', 'succeeded'),
          output('ME', 'F3'),
        ],
      }),
    )

    const wrapper = await mountView()
    expect(phaseRows(wrapper)).toEqual(['ME F1 | Done', 'ME F2 | Pending', 'ME F3 | Pending'])

    await vi.advanceTimersByTimeAsync(2000)

    expect(getCuttingJobMock).toHaveBeenCalledTimes(2)
    expect(cutsDone(wrapper)).toContain('2 of 3')
    expect(phaseRows(wrapper)).toEqual(['ME F1 | Done', 'ME F2 | Done', 'ME F3 | Pending'])
    const bar = wrapper.find('[role="progressbar"]')
    expect(bar.attributes('aria-valuenow')).toBe('67')
  })

  it('shows one column per camera when both were cut', async () => {
    getCuttingJobMock.mockResolvedValue(
      job({
        outputs: [output('ME', 'F1', 'succeeded'), output('ME', 'F1', 'pending', { camera: 'C2' })],
      }),
    )

    const wrapper = await mountView()

    const headers = wrapper.findAll('thead th').map((th) => th.text())
    expect(headers).toEqual(['Phase', 'C1', 'C2'])
    expect(phaseRows(wrapper)).toEqual(['ME F1 | Done | Pending'])
  })

  it('keeps polling a queued job until it starts', async () => {
    getCuttingJobMock
      .mockResolvedValueOnce(job({ status: 'queued', startedAt: null }))
      .mockResolvedValueOnce(job({ status: 'running' }))

    const wrapper = await mountView()
    expect(wrapper.find('[data-testid="job-status"]').text()).toBe('Queued')

    await vi.advanceTimersByTimeAsync(2000)

    expect(wrapper.find('[data-testid="job-status"]').text()).toBe('Running')
  })

  it('stops polling once the job has finished', async () => {
    getCuttingJobMock.mockResolvedValue(
      job({
        status: 'succeeded',
        finishedAt: '2026-09-24T10:05:00Z',
        outputs: [output('ME', 'F1', 'succeeded')],
      }),
    )

    const wrapper = await mountView()
    await vi.advanceTimersByTimeAsync(10_000)

    expect(getCuttingJobMock).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false)
    expect(cutsDone(wrapper)).toContain('1 of 1')
  })

  it("shows each failed phase's reason", async () => {
    getCuttingJobMock.mockResolvedValue(
      job({
        status: 'failed',
        outputs: [
          output('ME', 'F1', 'succeeded'),
          output('ME', 'F2', 'failed', { failureReason: 'No output file was produced.' }),
        ],
      }),
    )

    const wrapper = await mountView()

    expect(phaseRows(wrapper)).toEqual(['ME F1 | Done', 'ME F2 | Failed'])
    const failures = wrapper.find('[data-testid="failed-outputs"]').text()
    expect(failures).toContain('C1 ME F2')
    expect(failures).toContain('No output file was produced.')
  })

  // Vue Router reuses the mounted view when only `:id` changes.
  it('switches to another job when the route goes straight to it', async () => {
    let finishOldPoll: (value: unknown) => void = () => {}
    getCuttingJobMock
      .mockResolvedValueOnce(job({ id: 31, testId: 'T001' }))
      .mockReturnValueOnce(new Promise((resolve) => (finishOldPoll = resolve)))
      .mockResolvedValue(job({ id: 32, testId: 'T002' }))
    const wrapper = await mountView('31')
    await vi.advanceTimersByTimeAsync(2000)

    await wrapper.router.push('/cutting-jobs/32')
    await flushPromises()
    finishOldPoll(job({ id: 31, testId: 'T001' }))
    await flushPromises()

    expect(wrapper.find('h1').text()).toContain('Cutting job #32')
    expect(wrapper.text()).toContain('T002')
    expect(wrapper.text()).not.toContain('T001')
    await vi.advanceTimersByTimeAsync(2000)
    expect(getCuttingJobMock.mock.calls.map(([id]) => id)).toEqual([31, 31, 32, 32])
  })

  it('stops polling when the view is left', async () => {
    getCuttingJobMock.mockResolvedValue(job())

    const wrapper = await mountView()
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(10_000)

    expect(getCuttingJobMock).toHaveBeenCalledTimes(1)
  })

  it('keeps retrying after a failed poll, showing why meanwhile', async () => {
    getCuttingJobMock
      .mockResolvedValueOnce(job())
      .mockRejectedValueOnce(new Error('Failed to load the cutting job.'))
      .mockResolvedValueOnce(job({ status: 'succeeded' }))

    const wrapper = await mountView()
    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.find('[role="alert"]').text()).toBe('Failed to load the cutting job.')
    expect(wrapper.find('[data-testid="job-status"]').text()).toBe('Running')

    await vi.advanceTimersByTimeAsync(2000)

    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="job-status"]').text()).toBe('Succeeded')
  })

  it('says the job was not found, without retrying', async () => {
    getCuttingJobMock.mockRejectedValue(new CuttingJobNotFoundError('Cutting job not found.'))

    const wrapper = await mountView('999')
    await vi.advanceTimersByTimeAsync(10_000)

    expect(wrapper.find('[role="alert"]').text()).toBe('Cutting job not found.')
    expect(getCuttingJobMock).toHaveBeenCalledTimes(1)
  })

  it('says a non-numeric id was not found without asking the backend', async () => {
    const wrapper = await mountView('abc')

    expect(wrapper.find('[role="alert"]').text()).toBe('Cutting job not found.')
    expect(getCuttingJobMock).not.toHaveBeenCalled()
  })
  // Issue #168.
  it.each(['running', 'succeeded', 'failed', 'cancelled'])(
    'offers Cancel only while the job is queued, not when %s',
    async (status) => {
      getCuttingJobMock.mockResolvedValue(job({ status }))

      const wrapper = await mountView('31')

      expect(wrapper.find('[data-testid="cancel"]').exists()).toBe(false)
    },
  )

  it('cancels a queued job straight away and shows it as cancelled without polling', async () => {
    getCuttingJobMock.mockResolvedValue(job({ status: 'queued', outputs: [output('ME', 'F1')] }))
    cancelCuttingJobMock.mockResolvedValue(
      job({
        status: 'cancelled',
        finishedAt: '2026-09-28T12:00:00Z',
        sourceRetained: false,
        outputs: [output('ME', 'F1')],
      }),
    )
    const wrapper = await mountView('31')

    await wrapper.find('[data-testid="cancel"]').trigger('click')
    await flushPromises()

    expect(cancelCuttingJobMock).toHaveBeenCalledWith(31)
    expect(wrapper.find('[data-testid="job-status"]').text()).toBe('Cancelled')
    expect(wrapper.find('[data-testid="cancel"]').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(10000)
    expect(getCuttingJobMock).toHaveBeenCalledTimes(1)
  })

  it("shows why a cancel was refused and keeps the job's page usable", async () => {
    getCuttingJobMock.mockResolvedValue(job({ status: 'queued' }))
    cancelCuttingJobMock.mockRejectedValue(new Error('Only a queued cutting job can be cancelled.'))
    const wrapper = await mountView('31')

    await wrapper.find('[data-testid="cancel"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="cancel-error"]').text()).toBe(
      'Only a queued cutting job can be cancelled.',
    )
  })

  it("hides a cancelled job's per-phase progress, since none of its phases ran", async () => {
    getCuttingJobMock.mockResolvedValue(
      job({ status: 'cancelled', finishedAt: '2026-09-28T12:00:00Z' }),
    )

    const wrapper = await mountView('31')

    expect(wrapper.find('[data-testid="job-status"]').text()).toBe('Cancelled')
    expect(phaseRows(wrapper)).toEqual([])
    expect(wrapper.find('[data-testid="cuts-done"]').exists()).toBe(false)
  })

  // Issue #169.
  it.each([
    ['queued', true],
    ['running', true],
    ['succeeded', true],
    ['failed', false],
    ['cancelled', false],
  ])(
    'offers no Discard source for a %s job whose source retained is %s',
    async (status, retained) => {
      getCuttingJobMock.mockResolvedValue(job({ status, sourceRetained: retained }))

      const wrapper = await mountView('31')

      expect(wrapper.find('[data-testid="discard-source"]').exists()).toBe(false)
    },
  )

  it.each(['failed', 'cancelled'])(
    "asks a second time before discarding a %s job's source",
    async (status) => {
      getCuttingJobMock.mockResolvedValue(job({ status, sourceRetained: true }))
      const wrapper = await mountView('31')

      await wrapper.find('[data-testid="discard-source"]').trigger('click')

      expect(wrapper.text()).toContain('Discard permanently?')
      expect(wrapper.find('[data-testid="discard-source-confirm"]').text()).toBe('Discard')
      expect(wrapper.find('[data-testid="discard-source-keep"]').text()).toBe('Keep')
      expect(discardCuttingJobSourceMock).not.toHaveBeenCalled()
    },
  )

  it('keeps the source when Keep is chosen', async () => {
    getCuttingJobMock.mockResolvedValue(job({ status: 'failed', sourceRetained: true }))
    const wrapper = await mountView('31')

    await wrapper.find('[data-testid="discard-source"]').trigger('click')
    await wrapper.find('[data-testid="discard-source-keep"]').trigger('click')

    expect(wrapper.text()).not.toContain('Discard permanently?')
    expect(wrapper.find('[data-testid="discard-source"]').exists()).toBe(true)
    expect(discardCuttingJobSourceMock).not.toHaveBeenCalled()
  })

  it('discards the source once confirmed, leaving the job failed', async () => {
    getCuttingJobMock.mockResolvedValue(job({ status: 'failed', sourceRetained: true }))
    discardCuttingJobSourceMock.mockResolvedValue(job({ status: 'failed', sourceRetained: false }))
    const wrapper = await mountView('31')

    await wrapper.find('[data-testid="discard-source"]').trigger('click')
    await wrapper.find('[data-testid="discard-source-confirm"]').trigger('click')
    await flushPromises()

    expect(discardCuttingJobSourceMock).toHaveBeenCalledWith(31)
    expect(wrapper.find('[data-testid="job-status"]').text()).toBe('Failed')
    expect(wrapper.find('[data-testid="discard-source"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Discard permanently?')
  })

  it('shows why a discard failed and lets it be tried again', async () => {
    getCuttingJobMock.mockResolvedValue(job({ status: 'failed', sourceRetained: true }))
    discardCuttingJobSourceMock.mockRejectedValue(
      new Error("The source video couldn't be deleted. Try discarding it again."),
    )
    const wrapper = await mountView('31')

    await wrapper.find('[data-testid="discard-source"]').trigger('click')
    await wrapper.find('[data-testid="discard-source-confirm"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="discard-source-error"]').text()).toBe(
      "The source video couldn't be deleted. Try discarding it again.",
    )
    expect(wrapper.find('[data-testid="discard-source"]').exists()).toBe(true)
  })
})
