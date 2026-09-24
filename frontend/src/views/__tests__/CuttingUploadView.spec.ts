import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import CuttingUploadView from '../CuttingUploadView.vue'

const uploadSourceVideoMock = vi.hoisted(() => vi.fn())
const submitCuttingJobBatchMock = vi.hoisted(() => vi.fn())
const forgetSourceVideoUploadMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/cuttingJobs', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/cuttingJobs')>()),
  uploadSourceVideo: uploadSourceVideoMock,
  submitCuttingJobBatch: submitCuttingJobBatchMock,
  forgetSourceVideoUpload: forgetSourceVideoUploadMock,
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/cutting', name: 'cutting-upload', component: CuttingUploadView },
      {
        path: '/cutting-jobs/:id',
        name: 'cutting-job-detail',
        component: { template: '<div>job</div>' },
      },
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
    ],
  })
}

async function mountView() {
  const router = createTestRouter()
  router.push('/cutting')
  await router.isReady()

  const wrapper = mount(CuttingUploadView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

async function chooseFile(wrapper: VueWrapper, testId: string, name: string) {
  const file = new File(['bytes'], name)
  const input = wrapper.find(`[data-testid="${testId}"]`)
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  return file
}

function testEntries(wrapper: VueWrapper) {
  return wrapper.findAll('[data-testid="test-entry"]')
}

function submitButton(wrapper: VueWrapper) {
  return wrapper.find('[data-testid="submit-batch"]').element as HTMLButtonElement
}

function job(id: number, testId: string) {
  return {
    id,
    testId,
    requestedByIdentity: null,
    status: 'queued',
    referenceCamera: 'C1',
    createdAt: '2026-09-24T10:00:00Z',
    startedAt: null,
    finishedAt: null,
    outputs: [],
  }
}

describe('CuttingUploadView', () => {
  afterEach(() => {
    uploadSourceVideoMock.mockReset()
    submitCuttingJobBatchMock.mockReset()
    forgetSourceVideoUploadMock.mockReset()
  })

  it('lets a researcher add Tests to the batch up to five', async () => {
    const wrapper = await mountView()
    expect(testEntries(wrapper)).toHaveLength(1)

    const addTest = wrapper.find('[data-testid="add-test"]')
    for (let i = 0; i < 4; i++) await addTest.trigger('click')

    expect(testEntries(wrapper)).toHaveLength(5)
    expect((addTest.element as HTMLButtonElement).disabled).toBe(true)
  })

  it('removes a Test from the batch', async () => {
    const wrapper = await mountView()
    await wrapper.find('[data-testid="add-test"]').trigger('click')
    await testEntries(wrapper)[1]!.find('[data-testid="test-id-input"]').setValue('T002')

    await testEntries(wrapper)[0]!.find('[data-testid="remove-test"]').trigger('click')

    expect(testEntries(wrapper)).toHaveLength(1)
    expect(
      (testEntries(wrapper)[0]!.find('[data-testid="test-id-input"]').element as HTMLInputElement)
        .value,
    ).toBe('T002')
  })

  it("fills in the Test ID from a source video's filename", async () => {
    const wrapper = await mountView()

    await chooseFile(wrapper, 'c2-input', 'T513_C2_2026-01-01.mp4')

    const input = wrapper.find('[data-testid="test-id-input"]').element as HTMLInputElement
    expect(input.value).toBe('T513')
  })

  it('only allows submitting once there is a workbook and every Test has an ID and a video', async () => {
    const wrapper = await mountView()
    expect(submitButton(wrapper).disabled).toBe(true)

    await chooseFile(wrapper, 'c1-input', 'T001_C1_source.mp4')
    expect(submitButton(wrapper).disabled).toBe(true)

    await chooseFile(wrapper, 'excel-input', 'timestamps.xlsx')
    expect(submitButton(wrapper).disabled).toBe(false)

    await wrapper.find('[data-testid="add-test"]').trigger('click')
    expect(submitButton(wrapper).disabled).toBe(true)
  })

  async function fillTwoTests(wrapper: VueWrapper) {
    await chooseFile(wrapper, 'excel-input', 'timestamps.xlsx')
    const t1c1 = await chooseFile(wrapper, 'c1-input', 'T001_C1_a.mp4')
    await wrapper.find('[data-testid="add-test"]').trigger('click')
    const second = testEntries(wrapper)[1]!
    const t2c1 = new File(['bytes'], 'T002_C1_b.mp4')
    const t2c2 = new File(['bytes'], 'T002_C2_b.mp4')
    for (const [testId, file] of [
      ['c1-input', t2c1],
      ['c2-input', t2c2],
    ] as const) {
      const input = second.find(`[data-testid="${testId}"]`)
      Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
      await input.trigger('change')
    }
    return { t1c1, t2c1, t2c2 }
  }

  it("uploads every chosen video, submits one batch, and links each Test's job", async () => {
    uploadSourceVideoMock.mockImplementation(async (file: File) => `id-${file.name}`)
    submitCuttingJobBatchMock.mockResolvedValue([
      { testId: 'T001', job: job(31, 'T001'), error: null },
      { testId: 'T002', job: job(32, 'T002'), error: null },
    ])
    const wrapper = await mountView()
    const { t1c1, t2c1, t2c2 } = await fillTwoTests(wrapper)

    await wrapper.find('[data-testid="submit-batch"]').trigger('click')
    await flushPromises()

    expect(uploadSourceVideoMock.mock.calls.map(([file, target]) => [file, target])).toEqual([
      [t1c1, { testId: 'T001', camera: 'C1' }],
      [t2c1, { testId: 'T002', camera: 'C1' }],
      [t2c2, { testId: 'T002', camera: 'C2' }],
    ])
    expect(submitCuttingJobBatchMock).toHaveBeenCalledWith(expect.any(File), [
      { testId: 'T001', c1UploadId: 'id-T001_C1_a.mp4', c2UploadId: null },
      { testId: 'T002', c1UploadId: 'id-T002_C1_b.mp4', c2UploadId: 'id-T002_C2_b.mp4' },
    ])
    const links = wrapper.findAll('[data-testid="job-link"]')
    expect(links.map((link) => link.attributes('href'))).toEqual([
      '/cutting-jobs/31',
      '/cutting-jobs/32',
    ])
    expect(forgetSourceVideoUploadMock).toHaveBeenCalledWith(t2c2, {
      testId: 'T002',
      camera: 'C2',
    })
  })

  it('shows upload progress per video while it uploads', async () => {
    let finishUpload: (id: string) => void = () => {}
    uploadSourceVideoMock.mockImplementation(
      (_file: File, _target: unknown, options: { onProgress: (r: number, t: number) => void }) => {
        options.onProgress(25, 100)
        return new Promise((resolve) => (finishUpload = resolve))
      },
    )
    const wrapper = await mountView()
    await chooseFile(wrapper, 'excel-input', 'timestamps.xlsx')
    await chooseFile(wrapper, 'c1-input', 'T001_C1_a.mp4')

    await wrapper.find('[data-testid="submit-batch"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="upload-progress-C1"]').text()).toContain('25%')
    expect(submitButton(wrapper).disabled).toBe(true)
    finishUpload('x')
  })

  it("shows a Test's upload failure on that Test and still submits the others", async () => {
    uploadSourceVideoMock.mockImplementation(async (file: File) => {
      if (file.name.startsWith('T001')) {
        throw new Error("Filename doesn't match the declared Test/camera.")
      }
      return `id-${file.name}`
    })
    submitCuttingJobBatchMock.mockResolvedValue([
      { testId: 'T002', job: job(32, 'T002'), error: null },
    ])
    const wrapper = await mountView()
    await fillTwoTests(wrapper)

    await wrapper.find('[data-testid="submit-batch"]').trigger('click')
    await flushPromises()

    expect(testEntries(wrapper)[0]!.find('[role="alert"]').text()).toContain(
      "C1: Filename doesn't match the declared Test/camera.",
    )
    expect(submitCuttingJobBatchMock).toHaveBeenCalledWith(expect.any(File), [
      { testId: 'T002', c1UploadId: 'id-T002_C1_b.mp4', c2UploadId: 'id-T002_C2_b.mp4' },
    ])
    expect(testEntries(wrapper)[1]!.find('[data-testid="job-link"]').exists()).toBe(true)
  })

  it("shows the backend's validation error for a Test on that Test", async () => {
    uploadSourceVideoMock.mockImplementation(async (file: File) => `id-${file.name}`)
    submitCuttingJobBatchMock.mockResolvedValue([
      { testId: 'T001', job: job(31, 'T001'), error: null },
      {
        testId: 'T002',
        job: null,
        error: { detail: 'Row 4, column ME_F3: "-" is not a time value.', code: null },
      },
    ])
    const wrapper = await mountView()
    await fillTwoTests(wrapper)

    await wrapper.find('[data-testid="submit-batch"]').trigger('click')
    await flushPromises()

    expect(testEntries(wrapper)[0]!.find('[role="alert"]').exists()).toBe(false)
    expect(testEntries(wrapper)[1]!.find('[role="alert"]').text()).toContain(
      'Row 4, column ME_F3: "-" is not a time value.',
    )
    expect(testEntries(wrapper)[1]!.find('[data-testid="job-link"]').exists()).toBe(false)
  })

  it("doesn't ask for a confirmation it can't take when a Test already has Cuts", async () => {
    uploadSourceVideoMock.mockImplementation(async (file: File) => `id-${file.name}`)
    submitCuttingJobBatchMock.mockResolvedValue([
      {
        testId: 'T001',
        job: null,
        error: {
          detail: 'Cuts already exist for T001. Confirm to overwrite them.',
          code: 'cuts_already_exist',
        },
      },
    ])
    const wrapper = await mountView()
    await chooseFile(wrapper, 'excel-input', 'timestamps.xlsx')
    await chooseFile(wrapper, 'c1-input', 'T001_C1_a.mp4')

    await wrapper.find('[data-testid="submit-batch"]').trigger('click')
    await flushPromises()

    const alert = testEntries(wrapper)[0]!.find('[role="alert"]').text()
    expect(alert).toContain('T001 already has Cuts')
    expect(alert).not.toContain('Confirm')
  })

  it('on a second submit, retries only the Tests that got no job, resuming their uploads', async () => {
    let connectionDrops = true
    uploadSourceVideoMock.mockImplementation(async (file: File) => {
      if (file.name === 'T002_C2_b.mp4' && connectionDrops) {
        throw new Error('The connection was lost during the upload.')
      }
      return `id-${file.name}`
    })
    submitCuttingJobBatchMock.mockResolvedValueOnce([
      { testId: 'T001', job: job(31, 'T001'), error: null },
    ])
    const wrapper = await mountView()
    const { t2c2 } = await fillTwoTests(wrapper)
    await wrapper.find('[data-testid="submit-batch"]').trigger('click')
    await flushPromises()
    expect(testEntries(wrapper)[1]!.find('[role="alert"]').text()).toContain(
      'C2: The connection was lost during the upload.',
    )

    connectionDrops = false
    uploadSourceVideoMock.mockClear()
    submitCuttingJobBatchMock.mockResolvedValueOnce([
      { testId: 'T002', job: job(32, 'T002'), error: null },
    ])
    await wrapper.find('[data-testid="submit-batch"]').trigger('click')
    await flushPromises()

    // T002's C1 already finished, so only C2 goes back to the upload service
    // (which continues it from the bytes the backend already holds).
    expect(uploadSourceVideoMock.mock.calls.map(([file]) => file)).toEqual([t2c2])
    expect(submitCuttingJobBatchMock).toHaveBeenLastCalledWith(expect.any(File), [
      { testId: 'T002', c1UploadId: 'id-T002_C1_b.mp4', c2UploadId: 'id-T002_C2_b.mp4' },
    ])
    expect(wrapper.findAll('[data-testid="job-link"]')).toHaveLength(2)
  })

  it('shows why the whole batch was refused', async () => {
    uploadSourceVideoMock.mockImplementation(async (file: File) => `id-${file.name}`)
    submitCuttingJobBatchMock.mockRejectedValue(new Error('Too many requests. Try again later.'))
    const wrapper = await mountView()
    await fillTwoTests(wrapper)

    await wrapper.find('[data-testid="submit-batch"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="batch-error"]').text()).toContain(
      'Too many requests. Try again later.',
    )
  })
})
