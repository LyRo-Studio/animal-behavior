import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import { formatDate } from '@/utils/date'

import ConsolidationsHistoryView from '../ConsolidationsHistoryView.vue'

const listConsolidationsMock = vi.hoisted(() => vi.fn())
const downloadConsolidationMock = vi.hoisted(() => vi.fn())
const renameConsolidationMock = vi.hoisted(() => vi.fn())
const deleteConsolidationMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/consolidations', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/consolidations')>()),
  listConsolidations: listConsolidationsMock,
  downloadConsolidation: downloadConsolidationMock,
  renameConsolidation: renameConsolidationMock,
  deleteConsolidation: deleteConsolidationMock,
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
    renameConsolidationMock.mockReset()
    deleteConsolidationMock.mockReset()
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
    expect(rows[0].find('[data-testid="consolidation-history-status"]').text()).toBe('Completed')
    expect(rows[0].find('[data-testid="consolidation-history-created-at"]').text()).toBe(
      formatDate('2026-01-02T10:00:00Z'),
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

    expect(wrapper.find('[data-testid="consolidation-history-status"]').text()).toBe('Failed')
    expect(wrapper.find('[data-testid="consolidation-history-failure-reason"]').text()).toBe(
      "fases: ontbrekende kolommen ['duur_s']",
    )
    expect(wrapper.find('[data-testid="consolidation-history-download"]').exists()).toBe(false)
  })

  it('shows a consolidation still processing as such, with no download action', async () => {
    listConsolidationsMock.mockResolvedValue([
      consolidation({ status: 'processing', resultSizeBytes: null, completedAt: null }),
    ])

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="consolidation-history-status"]').text()).toBe('Processing…')
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

  describe('inline rename', () => {
    async function startRename(wrapper: Awaited<ReturnType<typeof mountView>>, rowIndex = 0) {
      const row = wrapper.findAll('[data-testid="consolidation-history-row"]')[rowIndex]
      await row.find('[data-testid="consolidation-history-rename"]').trigger('click')
      return row
    }

    it('opens an input prefilled with the current display name', async () => {
      listConsolidationsMock.mockResolvedValue([consolidation({ displayName: 'Pilot dogs' })])

      const wrapper = await mountView()
      const row = await startRename(wrapper)

      const input = row.find('[data-testid="consolidation-history-rename-input"]')
      expect((input.element as HTMLInputElement).value).toBe('Pilot dogs')
    })

    it('opens an empty input, hinting at the original filename, when there is no display name', async () => {
      listConsolidationsMock.mockResolvedValue([
        consolidation({ displayName: null, originalFilename: 'export.xlsx' }),
      ])

      const wrapper = await mountView()
      const row = await startRename(wrapper)

      const input = row.find('[data-testid="consolidation-history-rename-input"]')
      expect((input.element as HTMLInputElement).value).toBe('')
      expect(input.attributes('placeholder')).toBe('export.xlsx')
    })

    it('saves the new name and shows it, keeping the original filename visible', async () => {
      listConsolidationsMock.mockResolvedValue([
        consolidation({ id: 7, displayName: null, originalFilename: 'export.xlsx' }),
      ])
      renameConsolidationMock.mockResolvedValue(
        consolidation({
          id: 7,
          displayName: 'Pilot dogs, week 3',
          originalFilename: 'export.xlsx',
        }),
      )

      const wrapper = await mountView()
      const row = await startRename(wrapper)
      await row
        .find('[data-testid="consolidation-history-rename-input"]')
        .setValue('  Pilot dogs, week 3  ')
      await row.find('[data-testid="consolidation-history-rename-save"]').trigger('click')
      await flushPromises()

      expect(renameConsolidationMock).toHaveBeenCalledWith(7, 'Pilot dogs, week 3')
      expect(row.find('[data-testid="consolidation-history-rename-input"]').exists()).toBe(false)
      expect(row.find('[data-testid="consolidation-history-name"]').text()).toBe(
        'Pilot dogs, week 3',
      )
      expect(row.find('[data-testid="consolidation-history-original-filename"]').text()).toContain(
        'export.xlsx',
      )
    })

    it('saves on Enter', async () => {
      listConsolidationsMock.mockResolvedValue([consolidation({ id: 7 })])
      renameConsolidationMock.mockResolvedValue(consolidation({ id: 7, displayName: 'Renamed' }))

      const wrapper = await mountView()
      const row = await startRename(wrapper)
      const input = row.find('[data-testid="consolidation-history-rename-input"]')
      await input.setValue('Renamed')
      await input.trigger('keydown', { key: 'Enter' })
      await flushPromises()

      expect(renameConsolidationMock).toHaveBeenCalledWith(7, 'Renamed')
    })

    it('clears the display name when saved blank, falling back to the original filename', async () => {
      listConsolidationsMock.mockResolvedValue([
        consolidation({ id: 7, displayName: 'Pilot dogs', originalFilename: 'export.xlsx' }),
      ])
      renameConsolidationMock.mockResolvedValue(
        consolidation({ id: 7, displayName: null, originalFilename: 'export.xlsx' }),
      )

      const wrapper = await mountView()
      const row = await startRename(wrapper)
      await row.find('[data-testid="consolidation-history-rename-input"]').setValue('   ')
      await row.find('[data-testid="consolidation-history-rename-save"]').trigger('click')
      await flushPromises()

      expect(renameConsolidationMock).toHaveBeenCalledWith(7, null)
      expect(row.find('[data-testid="consolidation-history-name"]').text()).toBe('export.xlsx')
    })

    it('cancels without saving, on the cancel button or Escape', async () => {
      listConsolidationsMock.mockResolvedValue([consolidation({ displayName: 'Pilot dogs' })])

      const wrapper = await mountView()
      let row = await startRename(wrapper)
      await row.find('[data-testid="consolidation-history-rename-input"]').setValue('Other')
      await row.find('[data-testid="consolidation-history-rename-cancel"]').trigger('click')

      expect(row.find('[data-testid="consolidation-history-rename-input"]').exists()).toBe(false)
      expect(row.find('[data-testid="consolidation-history-name"]').text()).toBe('Pilot dogs')

      row = await startRename(wrapper)
      await row
        .find('[data-testid="consolidation-history-rename-input"]')
        .trigger('keydown', { key: 'Escape' })

      expect(row.find('[data-testid="consolidation-history-rename-input"]').exists()).toBe(false)
      expect(renameConsolidationMock).not.toHaveBeenCalled()
    })

    it('shows a rename error on its row and keeps the input open', async () => {
      listConsolidationsMock.mockResolvedValue([consolidation({ id: 8 }), consolidation({ id: 7 })])
      renameConsolidationMock.mockRejectedValue(new Error('Consolidation not found.'))

      const wrapper = await mountView()
      const row = await startRename(wrapper, 1)
      await row.find('[data-testid="consolidation-history-rename-input"]').setValue('Renamed')
      await row.find('[data-testid="consolidation-history-rename-save"]').trigger('click')
      await flushPromises()

      const rows = wrapper.findAll('[data-testid="consolidation-history-row"]')
      expect(rows[1].text()).toContain('Consolidation not found.')
      expect(rows[0].text()).not.toContain('Consolidation not found.')
      expect(rows[1].find('[data-testid="consolidation-history-rename-input"]').exists()).toBe(true)
    })

    it('limits the input to the backend maximum length', async () => {
      listConsolidationsMock.mockResolvedValue([consolidation()])

      const wrapper = await mountView()
      const row = await startRename(wrapper)

      expect(
        row.find('[data-testid="consolidation-history-rename-input"]').attributes('maxlength'),
      ).toBe('255')
    })
  })

  describe('delete', () => {
    function rowsOf(wrapper: Awaited<ReturnType<typeof mountView>>) {
      return wrapper.findAll('[data-testid="consolidation-history-row"]')
    }

    it('asks for confirmation before deleting anything', async () => {
      listConsolidationsMock.mockResolvedValue([consolidation({ id: 7 })])

      const wrapper = await mountView()
      await rowsOf(wrapper)[0].find('[data-testid="consolidation-history-delete"]').trigger('click')

      expect(deleteConsolidationMock).not.toHaveBeenCalled()
      expect(
        rowsOf(wrapper)[0].find('[data-testid="consolidation-history-delete-confirm"]').exists(),
      ).toBe(true)
    })

    it('deletes on confirmation and removes the row from the list', async () => {
      listConsolidationsMock.mockResolvedValue([
        consolidation({ id: 8, originalFilename: 'kept.xlsx' }),
        consolidation({ id: 7, originalFilename: 'deleted.xlsx' }),
      ])
      deleteConsolidationMock.mockResolvedValue(undefined)

      const wrapper = await mountView()
      const row = rowsOf(wrapper)[1]
      await row.find('[data-testid="consolidation-history-delete"]').trigger('click')
      await row.find('[data-testid="consolidation-history-delete-confirm"]').trigger('click')
      await flushPromises()

      expect(deleteConsolidationMock).toHaveBeenCalledWith(7)
      expect(rowsOf(wrapper)).toHaveLength(1)
      expect(wrapper.text()).toContain('kept.xlsx')
      expect(wrapper.text()).not.toContain('deleted.xlsx')
    })

    it('shows the empty state once the last consolidation is deleted', async () => {
      listConsolidationsMock.mockResolvedValue([consolidation({ id: 7 })])
      deleteConsolidationMock.mockResolvedValue(undefined)

      const wrapper = await mountView()
      const row = rowsOf(wrapper)[0]
      await row.find('[data-testid="consolidation-history-delete"]').trigger('click')
      await row.find('[data-testid="consolidation-history-delete-confirm"]').trigger('click')
      await flushPromises()

      expect(wrapper.text()).toContain('No consolidations yet.')
    })

    it('can back out of a delete without deleting', async () => {
      listConsolidationsMock.mockResolvedValue([consolidation({ id: 7 })])

      const wrapper = await mountView()
      const row = rowsOf(wrapper)[0]
      await row.find('[data-testid="consolidation-history-delete"]').trigger('click')
      await row.find('[data-testid="consolidation-history-delete-cancel"]').trigger('click')

      expect(deleteConsolidationMock).not.toHaveBeenCalled()
      expect(row.find('[data-testid="consolidation-history-delete-confirm"]').exists()).toBe(false)
      expect(rowsOf(wrapper)).toHaveLength(1)
    })

    it('keeps the row and shows the error on it when deleting fails', async () => {
      listConsolidationsMock.mockResolvedValue([consolidation({ id: 8 }), consolidation({ id: 7 })])
      deleteConsolidationMock.mockRejectedValue(new Error('Failed to delete consolidation.'))

      const wrapper = await mountView()
      const row = rowsOf(wrapper)[1]
      await row.find('[data-testid="consolidation-history-delete"]').trigger('click')
      await row.find('[data-testid="consolidation-history-delete-confirm"]').trigger('click')
      await flushPromises()

      expect(rowsOf(wrapper)).toHaveLength(2)
      expect(rowsOf(wrapper)[1].text()).toContain('Failed to delete consolidation.')
      expect(rowsOf(wrapper)[0].text()).not.toContain('Failed to delete consolidation.')
    })

    it('offers delete for completed and failed consolidations, but not one still processing', async () => {
      listConsolidationsMock.mockResolvedValue([
        consolidation({ id: 9, status: 'completed' }),
        consolidation({ id: 8, status: 'failed', failureReason: 'bad input' }),
        consolidation({ id: 7, status: 'processing' }),
      ])

      const wrapper = await mountView()
      const deletable = rowsOf(wrapper).map((row) =>
        row.find('[data-testid="consolidation-history-delete"]').exists(),
      )

      expect(deletable).toEqual([true, true, false])
    })
  })
})
