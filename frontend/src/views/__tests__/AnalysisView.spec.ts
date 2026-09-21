import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import AnalysisView from '../AnalysisView.vue'

const getAnalysisMock = vi.hoisted(() => vi.fn())
const cancelAnalysisMock = vi.hoisted(() => vi.fn())
const downloadAnalysisReportMock = vi.hoisted(() => vi.fn())
// A real class, not a plain mock: the view does `err instanceof
// AnalysisNotFoundError`, so the mocked module needs to export the same
// constructor identity that tests throw instances of.
const AnalysisNotFoundError = vi.hoisted(() => class AnalysisNotFoundError extends Error {})

vi.mock('@/services/analyses', () => ({
  getAnalysis: getAnalysisMock,
  cancelAnalysis: cancelAnalysisMock,
  downloadAnalysisReport: downloadAnalysisReportMock,
  AnalysisNotFoundError,
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/analyses/:id', name: 'analysis-detail', component: AnalysisView },
      { path: '/media', name: 'media', component: { template: '<div>media</div>' } },
    ],
  })
}

async function mountView(id: string | number = 42) {
  const router = createTestRouter()
  router.push(`/analyses/${id}`)
  await router.isReady()

  const wrapper = mount(AnalysisView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: 42,
    testId: 'T001',
    requestedByIdentity: 'jan.peeters@vives.be',
    status: 'queued',
    dogtraceVersion: null,
    reportAvailable: false,
    createdAt: '2026-01-01T00:00:00Z',
    startedAt: null,
    finishedAt: null,
    videos: [],
    ...overrides,
  }
}

function video(overrides: Record<string, unknown> = {}) {
  return {
    cutKey: 'cuts/T001/T001_C2_ME_F1.mp4',
    position: 0,
    status: 'pending',
    failureReason: null,
    ...overrides,
  }
}

describe('AnalysisView', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    getAnalysisMock.mockReset()
    cancelAnalysisMock.mockReset()
    downloadAnalysisReportMock.mockReset()
  })

  it('shows a not-found message for an unknown job', async () => {
    getAnalysisMock.mockRejectedValue(new AnalysisNotFoundError('Analysis not found.'))

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('Analysis not found.')
  })

  it('shows a not-found message for a non-numeric id without calling the API', async () => {
    const wrapper = await mountView('not-a-number')

    expect(wrapper.text()).toContain('Analysis not found.')
    expect(getAnalysisMock).not.toHaveBeenCalled()
  })

  it('polls a queued job and reflects a status change without a manual refresh', async () => {
    getAnalysisMock
      .mockResolvedValueOnce(job({ status: 'queued', videos: [video()] }))
      .mockResolvedValueOnce(job({ status: 'running', videos: [video({ status: 'processing' })] }))

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="status"]').text()).toBe('queued')
    expect(getAnalysisMock).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(2000)

    expect(getAnalysisMock).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="status"]').text()).toBe('running')
  })

  it('shows who ran the analysis', async () => {
    getAnalysisMock.mockResolvedValue(job({ status: 'completed' }))

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="requested-by"]').text()).toBe('jan.peeters@vives.be')
  })

  it('omits the "run by" line when the analysis has no recorded identity', async () => {
    getAnalysisMock.mockResolvedValue(job({ status: 'completed', requestedByIdentity: null }))

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="requested-by"]').exists()).toBe(false)
  })

  it('never schedules another poll if the component unmounts before an in-flight request resolves', async () => {
    let resolveFirst!: (value: unknown) => void
    getAnalysisMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFirst = resolve
      }),
    )

    const router = createTestRouter()
    router.push('/analyses/42')
    await router.isReady()
    const wrapper = mount(AnalysisView, { global: { plugins: [router] } })
    await flushPromises()

    wrapper.unmount()
    resolveFirst(job({ status: 'queued', videos: [video()] }))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(10000)

    // Only the one request the unmounted-but-still-in-flight fetch made —
    // its resolution must not have scheduled a next poll nobody can cancel.
    expect(getAnalysisMock).toHaveBeenCalledTimes(1)
  })

  it('ignores a stale poll response that resolves after cancel has already completed', async () => {
    let resolveSecondPoll!: (value: unknown) => void
    getAnalysisMock
      .mockResolvedValueOnce(job({ status: 'queued', videos: [video()] }))
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveSecondPoll = resolve
        }),
      )
    cancelAnalysisMock.mockResolvedValue(job({ status: 'cancelled', videos: [video()] }))

    const wrapper = await mountView()
    await vi.advanceTimersByTimeAsync(2000) // triggers the second, still-pending poll
    expect(getAnalysisMock).toHaveBeenCalledTimes(2)

    await wrapper.find('[data-testid="cancel"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="status"]').text()).toBe('cancelled')

    // The stale poll resolves after the cancel already landed, with an
    // outdated "queued" snapshot — must not resurrect it or its Cancel
    // button, or revive polling.
    resolveSecondPoll(job({ status: 'queued', videos: [video()] }))
    await flushPromises()

    expect(wrapper.find('[data-testid="status"]').text()).toBe('cancelled')
    expect(wrapper.find('[data-testid="cancel"]').exists()).toBe(false)

    await vi.advanceTimersByTimeAsync(10000)
    expect(getAnalysisMock).toHaveBeenCalledTimes(2)
  })

  it('shows the current video alongside the processed count while running', async () => {
    getAnalysisMock.mockResolvedValue(
      job({
        status: 'running',
        videos: [
          video({ status: 'succeeded' }),
          video({ cutKey: 'cuts/T001/T001_C2_ME_F2.mp4', status: 'processing' }),
          video({ cutKey: 'cuts/T001/T001_C2_ME_F3.mp4', status: 'pending' }),
        ],
      }),
    )

    const wrapper = await mountView()

    const summary = wrapper.find('[data-testid="progress-summary"]').text()
    expect(summary).toContain('2/3 videos')
    expect(summary).toContain('T001_C2_ME_F2.mp4')
  })

  it('stops polling once the job reaches a terminal state', async () => {
    getAnalysisMock.mockResolvedValue(
      job({ status: 'completed', videos: [video({ status: 'succeeded' })] }),
    )

    await mountView()
    await vi.advanceTimersByTimeAsync(10000)

    expect(getAnalysisMock).toHaveBeenCalledTimes(1)
  })

  it('shows a per-video success/failure breakdown with user-safe reasons on a terminal job', async () => {
    getAnalysisMock.mockResolvedValue(
      job({
        status: 'completed_with_errors',
        reportAvailable: true,
        videos: [
          video({ status: 'succeeded' }),
          video({
            cutKey: 'cuts/T001/T001_C2_ME_F2.mp4',
            status: 'failed',
            failureReason: 'The source video could not be retrieved from storage.',
          }),
        ],
      }),
    )

    const wrapper = await mountView()

    const summary = wrapper.find('[data-testid="terminal-summary"]')
    expect(summary.text()).toContain('1 succeeded, 1 failed')
    expect(summary.text()).toContain('T001_C2_ME_F2.mp4')
    expect(summary.text()).toContain('The source video could not be retrieved from storage.')
  })

  it('never shows the download button before the job is terminal', async () => {
    getAnalysisMock.mockResolvedValue(job({ status: 'running', videos: [video()] }))

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="download-report"]').exists()).toBe(false)
  })

  it('never shows the download button for a terminal job with no report', async () => {
    getAnalysisMock.mockResolvedValue(
      job({ status: 'failed', reportAvailable: false, videos: [video({ status: 'failed' })] }),
    )

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="download-report"]').exists()).toBe(false)
  })

  it('downloads the report when the job is terminal with a report available', async () => {
    getAnalysisMock.mockResolvedValue(
      job({
        status: 'completed',
        reportAvailable: true,
        videos: [video({ status: 'succeeded' })],
      }),
    )
    const blob = new Blob(['fake-xlsx-bytes'])
    downloadAnalysisReportMock.mockResolvedValue(blob)
    const objectUrl = 'blob:mock-url'
    const createObjectURL = vi.fn().mockReturnValue(objectUrl)
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const wrapper = await mountView()
    await wrapper.find('[data-testid="download-report"]').trigger('click')
    await flushPromises()

    expect(downloadAnalysisReportMock).toHaveBeenCalledWith(42)
    expect(createObjectURL).toHaveBeenCalledWith(blob)
    expect(clickSpy).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith(objectUrl)

    clickSpy.mockRestore()
    vi.unstubAllGlobals()
  })

  it('shows the backend-provided reason when the download fails', async () => {
    getAnalysisMock.mockResolvedValue(
      job({
        status: 'completed',
        reportAvailable: true,
        videos: [video({ status: 'succeeded' })],
      }),
    )
    downloadAnalysisReportMock.mockRejectedValue(new Error('Report not found.'))

    const wrapper = await mountView()
    await wrapper.find('[data-testid="download-report"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Report not found.')
  })

  it('shows the cancel button only while queued', async () => {
    getAnalysisMock.mockResolvedValue(job({ status: 'running', videos: [video()] }))

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="cancel"]').exists()).toBe(false)
  })

  it('cancels a queued job and updates the view to cancelled', async () => {
    getAnalysisMock.mockResolvedValue(job({ status: 'queued', videos: [video()] }))
    cancelAnalysisMock.mockResolvedValue(job({ status: 'cancelled', videos: [video()] }))

    const wrapper = await mountView()
    expect(wrapper.find('[data-testid="cancel"]').exists()).toBe(true)

    await wrapper.find('[data-testid="cancel"]').trigger('click')
    await flushPromises()

    expect(cancelAnalysisMock).toHaveBeenCalledWith(42)
    expect(wrapper.find('[data-testid="status"]').text()).toBe('cancelled')
    expect(wrapper.find('[data-testid="cancel"]').exists()).toBe(false)
  })

  it('shows the backend-provided reason when cancelling fails and leaves the job unchanged', async () => {
    getAnalysisMock.mockResolvedValue(job({ status: 'queued', videos: [video()] }))
    cancelAnalysisMock.mockRejectedValue(new Error('Only a queued analysis can be cancelled.'))

    const wrapper = await mountView()
    await wrapper.find('[data-testid="cancel"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Only a queued analysis can be cancelled.')
    expect(wrapper.find('[data-testid="status"]').text()).toBe('queued')
  })
})
