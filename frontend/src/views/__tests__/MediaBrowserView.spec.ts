import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import MediaBrowserView from '../MediaBrowserView.vue'

const listTestIdsMock = vi.hoisted(() => vi.fn())
const listCutsMock = vi.hoisted(() => vi.fn())
// A real class, not a plain mock: the view does `err instanceof
// TestNotFoundError`, so the mocked module needs to export the same
// constructor identity that tests throw instances of.
const TestNotFoundError = vi.hoisted(() => class TestNotFoundError extends Error {})

vi.mock('@/services/mediaBrowser', () => ({
  listTestIds: listTestIdsMock,
  listCuts: listCutsMock,
  TestNotFoundError,
}))

vi.mock('@/stores/session', () => ({
  session: { accessToken: { value: 'a-token' } },
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/media', name: 'media', component: MediaBrowserView },
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
    ],
  })
}

async function mountView() {
  const router = createTestRouter()
  router.push('/media')
  await router.isReady()
  const wrapper = mount(MediaBrowserView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

describe('MediaBrowserView', () => {
  afterEach(() => {
    listTestIdsMock.mockReset()
    listCutsMock.mockReset()
  })

  it('shows filtered suggestions as the user types', async () => {
    listTestIdsMock.mockResolvedValue(['T001', 'T002', 'T010'])
    const wrapper = await mountView()

    await wrapper.find('input#test-search').setValue('T00')

    const suggestions = wrapper.findAll('button[data-testid="test-suggestion"]')
    expect(suggestions.map((s) => s.text())).toEqual(['T001', 'T002'])
  })

  it("lists a Test's Cuts with parsed Camera/Condition/Phase and metadata", async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([
      {
        key: 'cuts/T001/T001_C1_ME_F1.mp4',
        filename: 'T001_C1_ME_F1.mp4',
        camera: 'C1',
        condition: 'ME',
        phase: 'F1',
        size: 2048,
        lastModified: '2026-01-01T12:00:00Z',
      },
    ])
    const wrapper = await mountView()

    await wrapper.find('input#test-search').setValue('T001')
    await wrapper.find('button[data-testid="test-suggestion"]').trigger('click')
    await flushPromises()

    expect(listCutsMock).toHaveBeenCalledWith('a-token', 'T001')
    expect(wrapper.text()).toContain('T001_C1_ME_F1.mp4')
    expect(wrapper.text()).toContain('C1')
    expect(wrapper.text()).toContain('ME')
    expect(wrapper.text()).toContain('F1')
    expect(wrapper.text()).toContain('2.0 KB')
  })

  it('falls back to a dash for a Cut with no parsed Camera/Condition/Phase', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([
      {
        key: 'cuts/T001/legacy.mp4',
        filename: 'legacy.mp4',
        camera: null,
        condition: null,
        phase: null,
        size: 10,
        lastModified: '2026-01-01T12:00:00Z',
      },
    ])
    const wrapper = await mountView()

    await wrapper.find('input#test-search').setValue('T001')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const row = wrapper.find('tbody tr')
    expect(row.text()).toContain('legacy.mp4')
    expect(row.text()).toContain('—')
  })

  it('shows a not-found state when searching an unknown Test ID', async () => {
    listTestIdsMock.mockResolvedValue([])
    listCutsMock.mockRejectedValue(new TestNotFoundError('Test "T999" not found.'))
    const wrapper = await mountView()

    await wrapper.find('input#test-search').setValue('T999')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-testid="not-found"]').exists()).toBe(true)
  })

  it('shows a generic error when loading Cuts fails for another reason', async () => {
    listTestIdsMock.mockResolvedValue([])
    listCutsMock.mockRejectedValue(new Error('Failed to load Cuts.'))
    const wrapper = await mountView()

    await wrapper.find('input#test-search').setValue('T001')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Failed to load Cuts.')
    expect(wrapper.find('[data-testid="not-found"]').exists()).toBe(false)
  })

  it('normalizes a typed-and-submitted Test ID to match the case-insensitive suggestions', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([])
    const wrapper = await mountView()

    await wrapper.find('input#test-search').setValue('t001')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(listCutsMock).toHaveBeenCalledWith('a-token', 'T001')
  })

  it('discards a slower, earlier search response once a later search has started', async () => {
    listTestIdsMock.mockResolvedValue(['T001', 'T002'])
    let resolveFirst!: (cuts: unknown[]) => void
    listCutsMock.mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
    listCutsMock.mockResolvedValueOnce([
      {
        key: 'cuts/T002/T002_C1_ME_F1.mp4',
        filename: 'T002_C1_ME_F1.mp4',
        camera: 'C1',
        condition: 'ME',
        phase: 'F1',
        size: 10,
        lastModified: '2026-01-01T12:00:00Z',
      },
    ])
    const wrapper = await mountView()

    await wrapper.find('input#test-search').setValue('T001')
    await wrapper.find('form').trigger('submit') // left pending
    await wrapper.find('input#test-search').setValue('T002')
    await wrapper.find('form').trigger('submit') // resolves immediately
    await flushPromises()

    // The stale T001 response arrives after T002 has already resolved.
    resolveFirst([
      {
        key: 'cuts/T001/T001_C1_ME_F1.mp4',
        filename: 'T001_C1_ME_F1.mp4',
        camera: 'C1',
        condition: 'ME',
        phase: 'F1',
        size: 10,
        lastModified: '2026-01-01T12:00:00Z',
      },
    ])
    await flushPromises()

    expect(wrapper.text()).toContain('Cuts for T002')
    expect(wrapper.text()).toContain('T002_C1_ME_F1.mp4')
    expect(wrapper.text()).not.toContain('T001_C1_ME_F1.mp4')
  })
})
