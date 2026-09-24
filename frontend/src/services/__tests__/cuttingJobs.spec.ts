import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  forgetSourceVideoUpload,
  getCuttingJob,
  submitCuttingJobBatch,
  uploadSourceVideo,
} from '../cuttingJobs'

// A fake of the backend's tus-like upload protocol (ticket #94): POST
// /cutting-jobs/uploads starts one, PATCH appends at `Upload-Offset`, GET reads
// the current offset back. It keeps the received bytes, so a test can check
// what the server ended up with rather than which requests were sent.
class FakeUploadServer {
  uploads = new Map<string, { meta: Record<string, unknown>; bytes: string }>()
  nextId = 1
  // Makes the next N PATCHes fail at the network level after writing half
  // their chunk, like a connection dropping mid-request.
  dropNextPatches = 0

  handle = async (input: string, init: RequestInit = {}): Promise<Response> => {
    const url = new URL(input, 'http://localhost')
    const method = init.method ?? 'GET'
    const uploadId = url.pathname.match(/\/cutting-jobs\/uploads\/(.+)$/)?.[1]

    if (method === 'POST' && url.pathname.endsWith('/cutting-jobs/uploads')) {
      const meta = JSON.parse(init.body as string)
      const id = `upload${this.nextId++}`
      this.uploads.set(id, { meta, bytes: '' })
      return json(201, this.status(id))
    }
    if (uploadId === undefined) throw new Error(`unexpected request ${method} ${input}`)
    const upload = this.uploads.get(uploadId)
    if (upload === undefined) return json(404, { detail: 'Upload not found.' })
    if (method === 'GET') return json(200, this.status(uploadId))

    const offset = Number((init.headers as Record<string, string>)['Upload-Offset'])
    if (offset !== upload.bytes.length) {
      return json(409, { detail: 'Offset mismatch.' })
    }
    const chunk = await readText(init.body as Blob)
    if (this.dropNextPatches > 0) {
      this.dropNextPatches--
      upload.bytes += chunk.slice(0, Math.ceil(chunk.length / 2))
      throw new TypeError('Failed to fetch')
    }
    upload.bytes += chunk
    return json(200, this.status(uploadId))
  }

  status(id: string) {
    const upload = this.uploads.get(id)!
    const total = upload.meta.total_size_bytes as number
    return {
      upload_id: id,
      test_id: upload.meta.test_id,
      camera: upload.meta.camera,
      filename: upload.meta.filename,
      total_size_bytes: total,
      received_bytes: upload.bytes.length,
      complete: upload.bytes.length >= total,
    }
  }
}

// jsdom's Blob has no `.text()`.
function readText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
  })
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

function video(content = 'abcdefghij', name = 'T001_C1_source.mp4'): File {
  return new File([content], name, { lastModified: 1_700_000_000_000 })
}

describe('uploading a source video', () => {
  let server: FakeUploadServer

  beforeEach(() => {
    server = new FakeUploadServer()
    vi.stubGlobal('fetch', vi.fn(server.handle))
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('sends a new file in chunks and returns its upload id', async () => {
    const uploadId = await uploadSourceVideo(
      video('abcdefghij'),
      { testId: 'T001', camera: 'C1' },
      {
        chunkSizeBytes: 4,
      },
    )

    expect(server.uploads.get(uploadId)?.bytes).toBe('abcdefghij')
    expect(server.uploads.get(uploadId)?.meta).toEqual({
      test_id: 'T001',
      camera: 'C1',
      filename: 'T001_C1_source.mp4',
      total_size_bytes: 10,
    })
  })

  it('resumes from the bytes the backend already has after a connection drops mid-chunk', async () => {
    server.dropNextPatches = 2

    const uploadId = await uploadSourceVideo(
      video('abcdefghij'),
      { testId: 'T001', camera: 'C1' },
      { chunkSizeBytes: 4, retryDelaysMs: [0, 0, 0] },
    )

    expect(server.uploads.size).toBe(1)
    expect(server.uploads.get(uploadId)?.bytes).toBe('abcdefghij')
  })

  it('gives up once the connection keeps dropping, keeping what was received', async () => {
    server.dropNextPatches = 10

    await expect(
      uploadSourceVideo(
        video('abcdefghij'),
        { testId: 'T001', camera: 'C1' },
        { chunkSizeBytes: 4, retryDelaysMs: [0, 0] },
      ),
    ).rejects.toThrow('The connection was lost')
    expect(server.uploads.get('upload1')?.bytes.length).toBeGreaterThan(0)
  })

  it('continues an earlier, unfinished upload of the same file instead of starting over', async () => {
    server.dropNextPatches = 10
    const file = video('abcdefghij')
    await uploadSourceVideo(
      file,
      { testId: 'T001', camera: 'C1' },
      { chunkSizeBytes: 4, retryDelaysMs: [] },
    ).catch(() => {})
    const receivedBeforeRetry = server.uploads.get('upload1')!.bytes.length
    server.dropNextPatches = 0
    const patchedFrom: number[] = []
    const handle = server.handle
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string, init: RequestInit = {}) => {
        if (init.method === 'PATCH') {
          patchedFrom.push(Number((init.headers as Record<string, string>)['Upload-Offset']))
        }
        return handle(input, init)
      }),
    )

    // e.g. after a page reload: a fresh File object for the same file on disk.
    const uploadId = await uploadSourceVideo(
      video('abcdefghij'),
      { testId: 'T001', camera: 'C1' },
      { chunkSizeBytes: 4 },
    )

    expect(uploadId).toBe('upload1')
    expect(server.uploads.size).toBe(1)
    expect(server.uploads.get('upload1')?.bytes).toBe('abcdefghij')
    expect(patchedFrom[0]).toBe(receivedBeforeRetry)
  })

  it('reuses a finished upload of the same file without sending it again', async () => {
    const first = await uploadSourceVideo(video(), { testId: 'T001', camera: 'C1' })
    const second = await uploadSourceVideo(video(), { testId: 'T001', camera: 'C1' })

    expect(second).toBe(first)
    expect(server.uploads.size).toBe(1)
  })

  it('starts a new upload for a different file, or the same file for another camera', async () => {
    await uploadSourceVideo(video('abcdefghij'), { testId: 'T001', camera: 'C1' })
    await uploadSourceVideo(video('0123456789012'), { testId: 'T001', camera: 'C1' })
    await uploadSourceVideo(video('abcdefghij', 'T001_C1_C2_x.mp4'), {
      testId: 'T001',
      camera: 'C2',
    })

    expect(server.uploads.size).toBe(3)
  })

  it('starts over when the remembered upload is gone from the backend', async () => {
    await uploadSourceVideo(video(), { testId: 'T001', camera: 'C1' })
    server.uploads.clear()

    const uploadId = await uploadSourceVideo(video(), { testId: 'T001', camera: 'C1' })

    expect(server.uploads.get(uploadId)?.bytes).toBe('abcdefghij')
  })

  it('starts a new upload once a forgotten file is sent again', async () => {
    const file = video()
    const target = { testId: 'T001', camera: 'C1' } as const
    const first = await uploadSourceVideo(file, target)

    forgetSourceVideoUpload(file, target)
    const second = await uploadSourceVideo(file, target)

    expect(second).not.toBe(first)
  })

  it("surfaces the backend's reason when it refuses to start an upload", async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json(400, { detail: "Filename doesn't match the declared Test/camera." })),
    )

    await expect(
      uploadSourceVideo(video('x', 'T002_C1_x.mp4'), { testId: 'T001', camera: 'C1' }),
    ).rejects.toThrow("Filename doesn't match the declared Test/camera.")
  })

  it('retries starting an upload when the connection drops', async () => {
    let failuresLeft = 1
    const handle = server.handle
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string, init: RequestInit = {}) => {
        if (init.method === 'POST' && failuresLeft > 0) {
          failuresLeft--
          throw new TypeError('Failed to fetch')
        }
        return handle(input, init)
      }),
    )

    const uploadId = await uploadSourceVideo(
      video('abcdefghij'),
      { testId: 'T001', camera: 'C1' },
      { retryDelaysMs: [0] },
    )

    expect(server.uploads.get(uploadId)?.bytes).toBe('abcdefghij')
  })

  it('reports progress as the backend confirms each chunk', async () => {
    const progress: [number, number][] = []

    await uploadSourceVideo(
      video('abcdefghij'),
      { testId: 'T001', camera: 'C1' },
      { chunkSizeBytes: 4, onProgress: (received, total) => progress.push([received, total]) },
    )

    expect(progress).toEqual([
      [0, 10],
      [4, 10],
      [8, 10],
      [10, 10],
    ])
  })

  it('retries a chunk the backend answered with a server error', async () => {
    let failuresLeft = 1
    const handle = server.handle
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string, init: RequestInit = {}) => {
        if (init.method === 'PATCH' && failuresLeft > 0) {
          failuresLeft--
          return json(502, {})
        }
        return handle(input, init)
      }),
    )

    const uploadId = await uploadSourceVideo(
      video('abcdefghij'),
      { testId: 'T001', camera: 'C1' },
      { chunkSizeBytes: 4, retryDelaysMs: [0] },
    )

    expect(server.uploads.get(uploadId)?.bytes).toBe('abcdefghij')
  })
})

const JOB_RESPONSE = {
  id: 12,
  test_id: 'T001',
  requested_by_identity: 'researcher@example.com',
  status: 'queued',
  reference_camera: 'C1',
  created_at: '2026-09-24T10:00:00Z',
  started_at: null,
  finished_at: null,
  outputs: [
    { camera: 'C1', condition: 'ME', phase: 'F1', status: 'pending', failure_reason: null },
  ],
}

// Ticket #97's POST /cutting-jobs/batch: one shared timestamp workbook plus a
// JSON `tests` list; 200 with one result per Test, in submission order.
describe('submitting a batch of cutting jobs', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    fetchMock.mockReset()
  })

  it("sends the workbook and every Test's uploads, and returns each Test's own outcome", async () => {
    fetchMock.mockResolvedValue(
      json(200, {
        results: [
          { test_id: 'T001', job: JOB_RESPONSE, error: null },
          {
            test_id: 'T002',
            job: null,
            error: {
              status_code: 400,
              detail: 'Row 3, column ME_F2: "-" is not a time.',
              code: null,
            },
          },
        ],
      }),
    )
    const excel = new File(['xlsx'], 'timestamps.xlsx')

    const results = await submitCuttingJobBatch(excel, [
      { testId: 'T001', c1UploadId: 'a', c2UploadId: null },
      { testId: 'T002', c1UploadId: null, c2UploadId: 'b' },
    ])

    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toMatch(/\/cutting-jobs\/batch$/)
    expect(init.method).toBe('POST')
    const body = init.body as FormData
    expect(body.get('excel')).toBeInstanceOf(File)
    expect(JSON.parse(body.get('tests') as string)).toEqual([
      { test_id: 'T001', c1_upload_id: 'a', c2_upload_id: null },
      { test_id: 'T002', c1_upload_id: null, c2_upload_id: 'b' },
    ])
    expect(results[0]).toMatchObject({
      testId: 'T001',
      job: { id: 12, testId: 'T001', status: 'queued', referenceCamera: 'C1' },
      error: null,
    })
    expect(results[1]).toEqual({
      testId: 'T002',
      job: null,
      error: { detail: 'Row 3, column ME_F2: "-" is not a time.', code: null },
    })
  })

  it("surfaces the backend's reason when the whole batch is refused", async () => {
    fetchMock.mockResolvedValue(json(400, { detail: 'The same Test appears twice.' }))

    await expect(
      submitCuttingJobBatch(new File(['x'], 't.xlsx'), [
        { testId: 'T001', c1UploadId: 'a', c2UploadId: null },
      ]),
    ).rejects.toThrow('The same Test appears twice.')
  })
})

describe('fetching a cutting job', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns the job with its per-phase outputs', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json(200, JOB_RESPONSE)),
    )

    const job = await getCuttingJob(12)

    expect(job).toEqual({
      id: 12,
      testId: 'T001',
      requestedByIdentity: 'researcher@example.com',
      status: 'queued',
      referenceCamera: 'C1',
      createdAt: '2026-09-24T10:00:00Z',
      startedAt: null,
      finishedAt: null,
      outputs: [
        { camera: 'C1', condition: 'ME', phase: 'F1', status: 'pending', failureReason: null },
      ],
    })
  })
})
