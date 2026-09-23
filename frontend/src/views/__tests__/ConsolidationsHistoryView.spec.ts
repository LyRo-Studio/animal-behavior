import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import ConsolidationsHistoryView from '../ConsolidationsHistoryView.vue'

const listConsolidationsMock = vi.hoisted(() => vi.fn())
const downloadConsolidationMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/consolidations', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/consolidations')>()),
  listConsolidations: listConsolidationsMock,
  downloadConsolidation: downloadConsolidationMock,
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      {
        path: '/consolidations',
        name: 'consolidations-history',
        component: ConsolidationsHistoryView,
      },
      {
        path: '/consolidation',
        name: 'consolidation',
        component: { template: '<div>consolidation</div>' },
      },
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
    ],
  })
}

function consolidation(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    originalFilename: 'export.xlsx',
    displayName: null,
    condition: 'ME_ZE',
    status: 'completed',
    requestedByIdentity: 'jan.peeters@vives.be',
    failureReason: null,
    inputSizeBytes: 1024,
    resultSizeBytes: 2048,
    createdAt: '2026-01-02T10:00:00Z',
    completedAt: '2026-01-02T10:00:03Z',
    ...overrides,
  }
}

async function mountView() {
  const router = createTestRouter()
  router.push('/consolidations')
  await router.isReady()

  const wrapper = mount(ConsolidationsHistoryView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

describe('ConsolidationsHistoryView', () => {
  afterEach(() => {
    listConsolidationsMock.mockReset()
    downloadConsolidationMock.mockReset()
  })

  it('loads and lists every consolidation with its filename, condition, status and date', async () => {
    listConsolidationsMock.mockResolvedValue([
      consolidation({ id: 8, originalFilename: 'second.xlsx', condition: 'ZE' }),
      consolidation({ id: 7, originalFilename: 'first.xlsx', condition: 'ME_ZE' }),
    ])

    const wrapper = await mountView()

    expect(listConsolidationsMock).toHaveBeenCalledWith()
    const rows = wrapper.findAll('[data-testid="consolidation-history-row"]')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('second.xlsx')
    expect(rows[0].find('[data-testid="consolidation-history-condition"]').text()).toBe('ZE')
    expect(rows[1].text()).toContain('first.xlsx')
    expect(rows[1].find('[data-testid="consolidation-history-condition"]').text()).toBe('ME + ZE')
    expect(rows[0].find('[data-testid="consolidation-history-status"]').text()).toContain(
      'completed',
    )
  })

  it('shows the display name when set, keeping the original filename visible', async () => {
    listConsolidationsMock.mockResolvedValue([
      consolidation({ displayName: 'Pilot dogs, week 3', originalFilename: 'export.xlsx' }),
    ])

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="consolidation-history-name"]').text()).toBe(
      'Pilot dogs, week 3',
    )
    expect(
      wrapper.find('[data-testid="consolidation-history-original-filename"]').text(),
    ).toContain('export.xlsx')
  })

  it('falls back to the original filename when there is no display name', async () => {
    listConsolidationsMock.mockResolvedValue([
      consolidation({ displayName: null, originalFilename: 'export.xlsx' }),
    ])

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="consolidation-history-name"]').text()).toBe('export.xlsx')
    expect(wrapper.find('[data-testid="consolidation-history-original-filename"]').exists()).toBe(
      false,
    )
  })

  it('shows a failed consolidation with its reason, and no download action', async () => {
    listConsolidationsMock.mockResolvedValue([
      consolidation({
        status: 'failed',
        failureReason: "fases: ontbrekende kolommen ['duur_s']",
        resultSizeBytes: null,
      }),
    ])

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="consolidation-history-status"]').text()).toContain('failed')
    expect(wrapper.find('[data-testid="consolidation-history-failure-reason"]').text()).toBe(
      "fases: ontbrekende kolommen ['duur_s']",
    )
    expect(wrapper.find('[data-testid="consolidation-history-download"]').exists()).toBe(false)
  })

  it('offers no download action for a consolidation still processing', async () => {
    listConsolidationsMock.mockResolvedValue([
      consolidation({ status: 'processing', resultSizeBytes: null, completedAt: null }),
    ])

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="consolidation-history-status"]').text()).toContain(
      'processing',
    )
    expect(wrapper.find('[data-testid="consolidation-history-download"]').exists()).toBe(false)
  })

  it('shows who ran each consolidation, and leaves it out when unattributed', async () => {
    listConsolidationsMock.mockResolvedValue([
      consolidation({ id: 8, requestedByIdentity: 'jan.peeters@vives.be' }),
      consolidation({ id: 7, requestedByIdentity: null }),
    ])

    const wrapper = await mountView()

    const runBy = wrapper.findAll('[data-testid="consolidation-history-requested-by"]')
    expect(runBy).toHaveLength(1)
    expect(runBy[0].text()).toContain('jan.peeters@vives.be')
  })

  it("downloads a completed consolidation's result from its row", async () => {
    listConsolidationsMock.mockResolvedValue([
      consolidation({ id: 7, originalFilename: 'export.xlsx' }),
    ])
    const blob = new Blob(['fake-consolidated-bytes'])
    downloadConsolidationMock.mockResolvedValue(blob)
    const objectUrl = 'blob:mock-url'
    const createObjectURL = vi.fn().mockReturnValue(objectUrl)
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const wrapper = await mountView()
    await wrapper.find('[data-testid="consolidation-history-download"]').trigger('click')
    await flushPromises()

    expect(downloadConsolidationMock).toHaveBeenCalledWith(7)
    expect(createObjectURL).toHaveBeenCalledWith(blob)
    expect(clickSpy).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith(objectUrl)

    clickSpy.mockRestore()
    vi.unstubAllGlobals()
  })

  it('shows a download error on the row it happened on', async () => {
    listConsolidationsMock.mockResolvedValue([consolidation({ id: 8 }), consolidation({ id: 7 })])
    downloadConsolidationMock.mockRejectedValue(new Error('Result not found.'))

    const wrapper = await mountView()
    const rows = wrapper.findAll('[data-testid="consolidation-history-row"]')
    await rows[1].find('[data-testid="consolidation-history-download"]').trigger('click')
    await flushPromises()

    expect(rows[1].text()).toContain('Result not found.')
    expect(rows[0].text()).not.toContain('Result not found.')
  })

  it('shows an empty state when there are no consolidations yet', async () => {
    listConsolidationsMock.mockResolvedValue([])

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('No consolidations yet.')
  })

  it('shows an error message when loading fails', async () => {
    listConsolidationsMock.mockRejectedValue(new Error('boom'))

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('Failed to load consolidations.')
  })
})
