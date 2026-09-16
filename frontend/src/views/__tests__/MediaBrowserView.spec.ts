import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import MediaBrowserView from '../MediaBrowserView.vue'

const listTestIdsMock = vi.hoisted(() => vi.fn())
const listCutsMock = vi.hoisted(() => vi.fn())
const listDatasetFolderMock = vi.hoisted(() => vi.fn())
const requestMediaTokenMock = vi.hoisted(() => vi.fn())
const mediaStreamUrlMock = vi.hoisted(() => vi.fn())
const getCutMediaInfoMock = vi.hoisted(() => vi.fn())
// A real class, not a plain mock: the view does `err instanceof
// TestNotFoundError`, so the mocked module needs to export the same
// constructor identity that tests throw instances of.
const TestNotFoundError = vi.hoisted(() => class TestNotFoundError extends Error {})
const DatasetFolderNotFoundError = vi.hoisted(
  () => class DatasetFolderNotFoundError extends Error {},
)
const CutInfoNotFoundError = vi.hoisted(() => class CutInfoNotFoundError extends Error {})

vi.mock('@/services/mediaBrowser', () => ({
  listTestIds: listTestIdsMock,
  listCuts: listCutsMock,
  listDatasetFolder: listDatasetFolderMock,
  requestMediaToken: requestMediaTokenMock,
  mediaStreamUrl: mediaStreamUrlMock,
  getCutMediaInfo: getCutMediaInfoMock,
  TestNotFoundError,
  DatasetFolderNotFoundError,
  CutInfoNotFoundError,
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
  beforeEach(() => {
    // Default: an empty top-level Dataset folder — tests that care about
    // Dataset browsing specifically override this.
    listDatasetFolderMock.mockResolvedValue([])
  })

  afterEach(() => {
    listTestIdsMock.mockReset()
    listCutsMock.mockReset()
    listDatasetFolderMock.mockReset()
    requestMediaTokenMock.mockReset()
    mediaStreamUrlMock.mockReset()
    getCutMediaInfoMock.mockReset()
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

  it('never lets a non Test-ID-shaped value into the search box', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    const wrapper = await mountView()
    const input = wrapper.find<HTMLInputElement>('input#test-search')

    await input.setValue('hallo')

    expect(input.element.value).toBe('')
    expect(wrapper.find('[data-testid="test-suggestions"]').exists()).toBe(false)

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(listCutsMock).not.toHaveBeenCalled()
  })

  it('strips invalid characters out of a typed value, keeping only a leading T and digits', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    const wrapper = await mountView()
    const input = wrapper.find<HTMLInputElement>('input#test-search')

    await input.setValue('T0a0x1')

    expect(input.element.value).toBe('T001')
  })

  it('does not submit a search for an incomplete Test ID (just "T")', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    const wrapper = await mountView()

    await wrapper.find('input#test-search').setValue('T')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(listCutsMock).not.toHaveBeenCalled()
  })

  it('closes the suggestion dropdown after picking a suggestion, even though the picked id still self-matches', async () => {
    listTestIdsMock.mockResolvedValue(['T011', 'T014'])
    listCutsMock.mockResolvedValue([])
    const wrapper = await mountView()

    await wrapper.find('input#test-search').setValue('T01')
    expect(wrapper.findAll('button[data-testid="test-suggestion"]')).toHaveLength(2)

    const t014 = wrapper
      .findAll('button[data-testid="test-suggestion"]')
      .find((btn) => btn.text() === 'T014')!
    await t014.trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="test-suggestions"]').exists()).toBe(false)
  })

  it('closes the suggestion dropdown after a typed-and-submitted search too', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([])
    const wrapper = await mountView()

    await wrapper.find('input#test-search').setValue('T001')
    expect(wrapper.find('[data-testid="test-suggestions"]').exists()).toBe(true)

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-testid="test-suggestions"]').exists()).toBe(false)
  })

  it('reopens the suggestion dropdown once the user edits the query again after a selection', async () => {
    listTestIdsMock.mockResolvedValue(['T011', 'T014'])
    listCutsMock.mockResolvedValue([])
    const wrapper = await mountView()

    await wrapper.find('input#test-search').setValue('T014')
    await wrapper.find('button[data-testid="test-suggestion"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="test-suggestions"]').exists()).toBe(false)

    await wrapper.find('input#test-search').setValue('T01')

    expect(wrapper.findAll('button[data-testid="test-suggestion"]')).toHaveLength(2)
  })

  it('loads and clearly distinguishes folders from files at the Dataset top level', async () => {
    listTestIdsMock.mockResolvedValue([])
    listDatasetFolderMock.mockResolvedValue([
      {
        name: 'dataset_v1',
        key: 'dataset/dataset_v1/',
        isFolder: true,
        size: null,
        lastModified: null,
      },
      {
        name: 'README.md',
        key: 'dataset/README.md',
        isFolder: false,
        size: 42,
        lastModified: '2026-01-01T12:00:00Z',
      },
    ])
    const wrapper = await mountView()

    expect(listDatasetFolderMock).toHaveBeenCalledWith('a-token', '')
    const folders = wrapper.findAll('[data-testid="dataset-folder"]')
    const files = wrapper.findAll('[data-testid="dataset-file"]')
    expect(folders.map((f) => f.text())).toEqual(['dataset_v1'])
    expect(files.map((f) => f.text())).toEqual(['README.md'])
    // A file is never a clickable button — no download action in this ticket.
    expect(files[0]!.element.tagName).not.toBe('BUTTON')
  })

  it('navigates into a Dataset folder and updates the breadcrumb', async () => {
    listTestIdsMock.mockResolvedValue([])
    listDatasetFolderMock.mockResolvedValueOnce([
      {
        name: 'dataset_v1',
        key: 'dataset/dataset_v1/',
        isFolder: true,
        size: null,
        lastModified: null,
      },
    ])
    listDatasetFolderMock.mockResolvedValueOnce([
      {
        name: 'train',
        key: 'dataset/dataset_v1/train/',
        isFolder: true,
        size: null,
        lastModified: null,
      },
    ])
    const wrapper = await mountView()

    await wrapper.find('[data-testid="dataset-folder"]').trigger('click')
    await flushPromises()

    expect(listDatasetFolderMock).toHaveBeenLastCalledWith('a-token', 'dataset_v1')
    expect(wrapper.find('nav[aria-label="Dataset folder path"]').text()).toContain('dataset_v1')
    expect(wrapper.find('[data-testid="dataset-folder"]').text()).toBe('train')
  })

  it('returns to a Dataset breadcrumb segment, dropping deeper path segments', async () => {
    listTestIdsMock.mockResolvedValue([])
    listDatasetFolderMock.mockResolvedValueOnce([
      {
        name: 'dataset_v1',
        key: 'dataset/dataset_v1/',
        isFolder: true,
        size: null,
        lastModified: null,
      },
    ])
    listDatasetFolderMock.mockResolvedValueOnce([
      {
        name: 'train',
        key: 'dataset/dataset_v1/train/',
        isFolder: true,
        size: null,
        lastModified: null,
      },
    ])
    listDatasetFolderMock.mockResolvedValueOnce([
      {
        name: 'dataset_v1',
        key: 'dataset/dataset_v1/',
        isFolder: true,
        size: null,
        lastModified: null,
      },
    ])
    const wrapper = await mountView()

    await wrapper.find('[data-testid="dataset-folder"]').trigger('click')
    await flushPromises()
    await wrapper.find('nav[aria-label="Dataset folder path"] button').trigger('click')
    await flushPromises()

    expect(listDatasetFolderMock).toHaveBeenLastCalledWith('a-token', '')
    expect(wrapper.find('nav[aria-label="Dataset folder path"]').text()).not.toContain('train')
  })

  it('shows a generic error when loading a Dataset folder fails', async () => {
    listTestIdsMock.mockResolvedValue([])
    listDatasetFolderMock.mockReset()
    listDatasetFolderMock.mockRejectedValue(new Error('Failed to load Dataset folder.'))
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('Failed to load Dataset folder.')
  })

  it('shows a not-found state when a Dataset folder disappears out from under navigation', async () => {
    listTestIdsMock.mockResolvedValue([])
    listDatasetFolderMock.mockReset()
    listDatasetFolderMock.mockRejectedValue(new DatasetFolderNotFoundError('gone'))
    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="dataset-not-found"]').exists()).toBe(true)
  })

  const SAMPLE_CUT = {
    key: 'cuts/T001/T001_C1_ME_F1.mp4',
    filename: 'T001_C1_ME_F1.mp4',
    camera: 'C1',
    condition: 'ME',
    phase: 'F1',
    size: 2048,
    lastModified: '2026-01-01T12:00:00Z',
  }

  async function searchForSampleCut(wrapper: Awaited<ReturnType<typeof mountView>>) {
    await wrapper.find('input#test-search').setValue('T001')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
  }

  it('plays a Cut by minting a play token and rendering the resulting stream URL', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([SAMPLE_CUT])
    requestMediaTokenMock.mockResolvedValue('play-token')
    mediaStreamUrlMock.mockReturnValue('https://api.example/media/stream?action=play')
    const wrapper = await mountView()
    await searchForSampleCut(wrapper)

    await wrapper.find('[data-testid="play-cut"]').trigger('click')
    await flushPromises()

    expect(requestMediaTokenMock).toHaveBeenCalledWith('a-token', SAMPLE_CUT.key, 'play')
    expect(mediaStreamUrlMock).toHaveBeenCalledWith(SAMPLE_CUT.key, 'play', 'play-token')
    const player = wrapper.find('[data-testid="cut-player"]')
    expect(player.exists()).toBe(true)
    expect(player.attributes('src')).toBe('https://api.example/media/stream?action=play')
  })

  it('closes the player when "Close player" is clicked', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([SAMPLE_CUT])
    requestMediaTokenMock.mockResolvedValue('play-token')
    mediaStreamUrlMock.mockReturnValue('https://api.example/media/stream')
    const wrapper = await mountView()
    await searchForSampleCut(wrapper)
    await wrapper.find('[data-testid="play-cut"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="cut-player"]').exists()).toBe(true)

    await wrapper.find('[data-testid="close-player"]').trigger('click')

    expect(wrapper.find('[data-testid="cut-player"]').exists()).toBe(false)
  })

  it('downloads a Cut by minting a download token and clicking a link to the stream URL', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([SAMPLE_CUT])
    requestMediaTokenMock.mockResolvedValue('download-token')
    mediaStreamUrlMock.mockReturnValue('https://api.example/media/stream?action=download')
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const wrapper = await mountView()
    await searchForSampleCut(wrapper)

    await wrapper.find('[data-testid="download-cut"]').trigger('click')
    await flushPromises()

    expect(requestMediaTokenMock).toHaveBeenCalledWith('a-token', SAMPLE_CUT.key, 'download')
    expect(mediaStreamUrlMock).toHaveBeenCalledWith(SAMPLE_CUT.key, 'download', 'download-token')
    expect(clickSpy).toHaveBeenCalledOnce()
    clickSpy.mockRestore()
  })

  it('shows an error and never opens a player when minting a play token fails', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([SAMPLE_CUT])
    requestMediaTokenMock.mockRejectedValue(new Error('Failed to start playback.'))
    const wrapper = await mountView()
    await searchForSampleCut(wrapper)

    await wrapper.find('[data-testid="play-cut"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Failed to start playback.')
    expect(wrapper.find('[data-testid="cut-player"]').exists()).toBe(false)
  })

  it('closes an open player when a new search starts', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([SAMPLE_CUT])
    requestMediaTokenMock.mockResolvedValue('play-token')
    mediaStreamUrlMock.mockReturnValue('https://api.example/media/stream')
    const wrapper = await mountView()
    await searchForSampleCut(wrapper)
    await wrapper.find('[data-testid="play-cut"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="cut-player"]').exists()).toBe(true)

    listCutsMock.mockResolvedValue([])
    await wrapper.find('input#test-search').setValue('T002')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-testid="cut-player"]').exists()).toBe(false)
  })

  it("shows a Cut's probed media info after clicking Info", async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([SAMPLE_CUT])
    getCutMediaInfoMock.mockResolvedValue({
      durationSeconds: 125,
      width: 1920,
      height: 1080,
      codec: 'h264',
    })
    const wrapper = await mountView()
    await searchForSampleCut(wrapper)

    await wrapper.find('[data-testid="info-cut"]').trigger('click')
    await flushPromises()

    expect(getCutMediaInfoMock).toHaveBeenCalledWith('a-token', SAMPLE_CUT.key)
    const panel = wrapper.find('[data-testid="cut-info-panel"]')
    expect(panel.exists()).toBe(true)
    expect(panel.find('[data-testid="cut-info-duration"]').text()).toBe('2:05')
    expect(panel.find('[data-testid="cut-info-resolution"]').text()).toBe('1920x1080')
    expect(panel.find('[data-testid="cut-info-codec"]').text()).toBe('h264')
  })

  it('toggles the info panel closed without re-fetching, then reopens without a second request', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([SAMPLE_CUT])
    getCutMediaInfoMock.mockResolvedValue({
      durationSeconds: 10,
      width: 640,
      height: 480,
      codec: 'vp9',
    })
    const wrapper = await mountView()
    await searchForSampleCut(wrapper)

    await wrapper.find('[data-testid="info-cut"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="cut-info-panel"]').exists()).toBe(true)

    await wrapper.find('[data-testid="info-cut"]').trigger('click')
    expect(wrapper.find('[data-testid="cut-info-panel"]').exists()).toBe(false)

    await wrapper.find('[data-testid="info-cut"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="cut-info-panel"]').exists()).toBe(true)
    expect(getCutMediaInfoMock).toHaveBeenCalledOnce()
  })

  it('shows an error in the info panel when loading media info fails', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([SAMPLE_CUT])
    getCutMediaInfoMock.mockRejectedValue(new Error('Failed to load media info.'))
    const wrapper = await mountView()
    await searchForSampleCut(wrapper)

    await wrapper.find('[data-testid="info-cut"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="cut-info-panel"]').text()).toContain(
      'Failed to load media info.',
    )
  })

  it('shows a not-found message in the info panel when the Cut no longer exists', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([SAMPLE_CUT])
    getCutMediaInfoMock.mockRejectedValue(new CutInfoNotFoundError('Cut not found.'))
    const wrapper = await mountView()
    await searchForSampleCut(wrapper)

    await wrapper.find('[data-testid="info-cut"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="cut-info-panel"]').text()).toContain('Cut not found.')
  })

  it('closes an open info panel when a new search starts', async () => {
    listTestIdsMock.mockResolvedValue(['T001'])
    listCutsMock.mockResolvedValue([SAMPLE_CUT])
    getCutMediaInfoMock.mockResolvedValue({
      durationSeconds: 10,
      width: 640,
      height: 480,
      codec: 'vp9',
    })
    const wrapper = await mountView()
    await searchForSampleCut(wrapper)
    await wrapper.find('[data-testid="info-cut"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="cut-info-panel"]').exists()).toBe(true)

    listCutsMock.mockResolvedValue([])
    await wrapper.find('input#test-search').setValue('T002')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-testid="cut-info-panel"]').exists()).toBe(false)
  })
})
