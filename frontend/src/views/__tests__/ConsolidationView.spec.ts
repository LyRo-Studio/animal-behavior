import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import ConsolidationView from '../ConsolidationView.vue'

const createConsolidationMock = vi.hoisted(() => vi.fn())
const downloadConsolidationMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/consolidations', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/consolidations')>()),
  createConsolidation: createConsolidationMock,
  downloadConsolidation: downloadConsolidationMock,
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/consolidation', name: 'consolidation', component: ConsolidationView },
      {
        path: '/consolidations',
        name: 'consolidations-history',
        component: { template: '<div>history</div>' },
      },
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
    ],
  })
}

async function mountView() {
  const router = createTestRouter()
  router.push('/consolidation')
  await router.isReady()

  const wrapper = mount(ConsolidationView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

async function selectFile(wrapper: Awaited<ReturnType<typeof mountView>>, name = 'export.xlsx') {
  const file = new File(['fake-excel-bytes'], name, { type: 'application/octet-stream' })
  const input = wrapper.find('[data-testid="file-input"]')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  return file
}

function consolidation(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    originalFilename: 'export.xlsx',
    displayName: null,
    condition: 'ME_ZE',
    status: 'completed',
    requestedByIdentity: null,
    failureReason: null,
    inputSizeBytes: 1024,
    resultSizeBytes: 2048,
    createdAt: '2026-09-23T00:00:00Z',
    completedAt: '2026-09-23T00:00:01Z',
    ...overrides,
  }
}

describe('ConsolidationView', () => {
  afterEach(() => {
    createConsolidationMock.mockReset()
    downloadConsolidationMock.mockReset()
  })

  it('disables the Consolidate button until a file is selected', async () => {
    const wrapper = await mountView()

    const button = wrapper.find('[data-testid="consolidate"]')
    expect((button.element as HTMLButtonElement).disabled).toBe(true)

    await selectFile(wrapper)

    expect((button.element as HTMLButtonElement).disabled).toBe(false)
  })

  it('shows the selected filename', async () => {
    const wrapper = await mountView()

    await selectFile(wrapper, 'my-observer-export.xlsx')

    expect(wrapper.text()).toContain('my-observer-export.xlsx')
  })

  it('submits the file and selected condition, showing a processing state meanwhile', async () => {
    let resolveCreate: (value: unknown) => void = () => {}
    createConsolidationMock.mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve
      }),
    )

    const wrapper = await mountView()
    await selectFile(wrapper)
    await wrapper.find('[data-testid="condition-select"]').setValue('ZE')
    await wrapper.find('[data-testid="consolidate"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="status-processing"]').exists()).toBe(true)
    expect(createConsolidationMock).toHaveBeenCalledWith(expect.any(File), 'ZE')

    resolveCreate(consolidation())
    await flushPromises()

    expect(wrapper.find('[data-testid="status-processing"]').exists()).toBe(false)
  })

  it('shows a completed consolidation with its source and creation date', async () => {
    createConsolidationMock.mockResolvedValue(
      consolidation({ originalFilename: 'export.xlsx', createdAt: '2026-09-23T10:00:00Z' }),
    )

    const wrapper = await mountView()
    await selectFile(wrapper)
    await wrapper.find('[data-testid="consolidate"]').trigger('click')
    await flushPromises()

    const completed = wrapper.find('[data-testid="consolidation-completed"]')
    expect(completed.exists()).toBe(true)
    expect(completed.text()).toContain('export.xlsx')
    expect(wrapper.find('[data-testid="download-consolidation"]').exists()).toBe(true)
  })

  it('shows a failed consolidation with its failure reason, and no download button', async () => {
    createConsolidationMock.mockResolvedValue(
      consolidation({ status: 'failed', failureReason: 'fases: ontbrekende kolommen' }),
    )

    const wrapper = await mountView()
    await selectFile(wrapper)
    await wrapper.find('[data-testid="consolidate"]').trigger('click')
    await flushPromises()

    const failed = wrapper.find('[data-testid="consolidation-failed"]')
    expect(failed.exists()).toBe(true)
    expect(failed.text()).toContain('fases: ontbrekende kolommen')
    expect(wrapper.find('[data-testid="download-consolidation"]').exists()).toBe(false)
  })

  it('shows a consolidation still processing as such, with no download button', async () => {
    createConsolidationMock.mockResolvedValue(
      consolidation({ status: 'processing', resultSizeBytes: null, completedAt: null }),
    )

    const wrapper = await mountView()
    await selectFile(wrapper)
    await wrapper.find('[data-testid="consolidate"]').trigger('click')
    await flushPromises()

    const processing = wrapper.find('[data-testid="consolidation-processing"]')
    expect(processing.exists()).toBe(true)
    expect(processing.text()).toContain('Processing…')
    expect(processing.text()).toContain('export.xlsx')
    expect(wrapper.find('[data-testid="download-consolidation"]').exists()).toBe(false)
  })

  it('shows the backend-provided reason when the request itself is rejected', async () => {
    createConsolidationMock.mockRejectedValue(new Error('File must be an .xlsx Excel file.'))

    const wrapper = await mountView()
    await selectFile(wrapper)
    await wrapper.find('[data-testid="consolidate"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('File must be an .xlsx Excel file.')
  })

  it('downloads the result when the download button is clicked', async () => {
    createConsolidationMock.mockResolvedValue(consolidation({ id: 7 }))
    const blob = new Blob(['fake-consolidated-bytes'])
    downloadConsolidationMock.mockResolvedValue(blob)
    const objectUrl = 'blob:mock-url'
    const createObjectURL = vi.fn().mockReturnValue(objectUrl)
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    const wrapper = await mountView()
    await selectFile(wrapper)
    await wrapper.find('[data-testid="consolidate"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="download-consolidation"]').trigger('click')
    await flushPromises()

    expect(downloadConsolidationMock).toHaveBeenCalledWith(7)
    expect(createObjectURL).toHaveBeenCalledWith(blob)
    expect(clickSpy).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith(objectUrl)

    clickSpy.mockRestore()
    vi.unstubAllGlobals()
  })

  it('links to the consolidation history', async () => {
    const wrapper = await mountView()

    const link = wrapper.find('[data-testid="consolidation-history-link"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('/consolidations')
  })
})
