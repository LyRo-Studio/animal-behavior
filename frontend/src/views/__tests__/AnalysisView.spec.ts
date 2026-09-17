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
const AnalysisUnauthorizedError = vi.hoisted(() => class AnalysisUnauthorizedError extends Error {})

vi.mock('@/services/analyses', () => ({
  getAnalysis: getAnalysisMock,
  cancelAnalysis: cancelAnalysisMock,
  downloadAnalysisReport: downloadAnalysisReportMock,
  AnalysisNotFoundError,
  AnalysisUnauthorizedError,
}))

// A real, mutable ref-like object (not a fresh `{ value: 'a-token' }` per
// test) so refreshAccessTokenMock's implementation can simulate a real
// rotation by writing a new value the view's next getAnalysis call reads.
const sessionAccessToken = vi.hoisted(() => ({ value: 'a-token' }))
const refreshAccessTokenMock = vi.hoisted(() => vi.fn())

vi.mock('@/stores/session', () => ({
  session: { accessToken: sessionAccessToken, refreshAccessToken: refreshAccessTokenMock },
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/analyses/:id', name: 'analysis-detail', component: AnalysisView },
      { path: '/media', name: 'media', component: { template: '<div>media</div>' } },
      { path: '/login', name: 'login', component: { template: '<div>login</div>' } },
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
    refreshAccessTokenMock.mockReset()
    sessionAccessToken.value = 'a-token'
  })

  it("shows a not-found message for an unknown or another Account's job", async () => {
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

  it('stops polling and replaces a stale status with a session-expired message when refreshing the token also fails', async () => {
    getAnalysisMock
      .mockResolvedValueOnce(job({ status: 'queued', videos: [video()] }))
      .mockRejectedValue(new AnalysisUnauthorizedError('Not authenticated'))
    // The refresh token itself is also expired/invalid — there's nothing
    // left to silently recover with.
    refreshAccessTokenMock.mockResolvedValue(false)

    const wrapper = await mountView()
    expect(wrapper.find('[data-testid="status"]').text()).toBe('queued')

    await vi.advanceTimersByTimeAsync(2000)
    expect(refreshAccessTokenMock).toHaveBeenCalledTimes(1)
    // A failed rotation means no retry is attempted — just the one 401'd
    // poll on top of the initial successful load.
    expect(getAnalysisMock).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="session-expired"]').exists()).toBe(true)
    // The stale "queued" snapshot from before the token expired must not
    // still be on screen next to the new message.
    expect(wrapper.find('[data-testid="status"]').exists()).toBe(false)

    // Must not keep hammering the backend with an access token that will
    // never become valid again on its own.
    await vi.advanceTimersByTimeAsync(10000)
    expect(getAnalysisMock).toHaveBeenCalledTimes(2)
  })

  it('silently rotates the access token and retries a 401 without ever showing a message', async () => {
    getAnalysisMock
      .mockResolvedValueOnce(job({ status: 'queued', videos: [video()] }))
      .mockRejectedValueOnce(new AnalysisUnauthorizedError('Not authenticated'))
      .mockResolvedValueOnce(job({ status: 'running', videos: [video({ status: 'processing' })] }))
    refreshAccessTokenMock.mockImplementation(async () => {
      sessionAccessToken.value = 'rotated-token'
      return true
    })

    const wrapper = await mountView()
    await vi.advanceTimersByTimeAsync(2000)

    expect(refreshAccessTokenMock).toHaveBeenCalledTimes(1)
    // The initial load, the 401'd poll, and the immediate post-rotation
    // retry — the retry must use the freshly rotated token, not the stale
    // one that just got rejected.
    expect(getAnalysisMock).toHaveBeenCalledTimes(3)
    expect(getAnalysisMock).toHaveBeenNthCalledWith(3, 'rotated-token', 42)
    expect(wrapper.find('[data-testid="session-expired"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="status"]').text()).toBe('running')
  })

  it('gives up after one retry if the rotated token also gets a 401, rather than looping', async () => {
    getAnalysisMock
      .mockResolvedValueOnce(job({ status: 'queued', videos: [video()] }))
      .mockRejectedValue(new AnalysisUnauthorizedError('Not authenticated'))
    refreshAccessTokenMock.mockResolvedValue(true)

    const wrapper = await mountView()
    await vi.advanceTimersByTimeAsync(2000)

    // Exactly one rotation attempt, not one per retried 401 — a second
    // rotation attempt on the retry's own 401 would risk looping forever
    // against a backend that keeps rejecting the "rotated" token.
    expect(refreshAccessTokenMock).toHaveBeenCalledTimes(1)
    expect(getAnalysisMock).toHaveBeenCalledTimes(3)
    expect(wrapper.find('[data-testid="session-expired"]').exists()).toBe(true)
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

    expect(downloadAnalysisReportMock).toHaveBeenCalledWith('a-token', 42)
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

    expect(cancelAnalysisMock).toHaveBeenCalledWith('a-token', 42)
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
