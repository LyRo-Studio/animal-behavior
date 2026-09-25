import { API_BASE_URL, errorFromResponse } from '@/services/apiBase'

// Ticket #100, part of issue #93's Feature C: the browser side of Ingestion —
// uploading a Test's C1/C2 source video(s) through the backend's tus-like
// protocol (ticket #94) and submitting up to 5 Tests as one batch (#97).

export type Camera = 'C1' | 'C2'

export interface SourceVideoTarget {
  testId: string
  camera: Camera
}

interface CuttingUploadResponse {
  upload_id: string
  test_id: string
  camera: Camera
  filename: string
  total_size_bytes: number
  received_bytes: number
  complete: boolean
}

// Well under nginx's shared 30 MiB `client_max_body_size`
// (nginx/snippets/server-common.conf), so one chunk is always one request the
// proxy accepts, and a dropped connection costs at most this much re-sent.
export const DEFAULT_UPLOAD_CHUNK_SIZE_BYTES = 8 * 1024 * 1024

// How long to wait before each successive retry after the connection drops
// (or the backend answers 5xx, e.g. mid-restart), before giving up. Reset
// after every chunk that does get through, so a flaky connection only fails
// the upload once it stays down for all of these in a row.
export const DEFAULT_UPLOAD_RETRY_DELAYS_MS = [1_000, 3_000, 10_000, 30_000]

export interface UploadSourceVideoOptions {
  chunkSizeBytes?: number
  retryDelaysMs?: number[]
  // Bytes the backend has confirmed so far, called once up front and again
  // after each chunk.
  onProgress?: (receivedBytes: number, totalBytes: number) => void
}

// A request that never got a usable answer: the connection failed or the
// backend answered 5xx. Worth retrying, unlike a 4xx.
class TransientUploadError extends Error {}

async function uploadRequest(url: string, init: RequestInit, fallback: string): Promise<Response> {
  let response: Response
  try {
    response = await fetch(url, init)
  } catch {
    throw new TransientUploadError('The connection was lost during the upload.')
  }
  if (response.status >= 500) {
    throw new TransientUploadError(fallback)
  }
  return response
}

function uploadUrl(uploadId: string): string {
  return `${API_BASE_URL}/cutting-jobs/uploads/${encodeURIComponent(uploadId)}`
}

// A lost response here can leave an unused, empty upload behind on the
// backend once retried. That's harmless: an empty upload holds no storage
// against the cap, which counts received bytes.
async function startUpload(file: File, target: SourceVideoTarget): Promise<CuttingUploadResponse> {
  const response = await uploadRequest(
    `${API_BASE_URL}/cutting-jobs/uploads`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        test_id: target.testId,
        camera: target.camera,
        filename: file.name,
        total_size_bytes: file.size,
      }),
    },
    'Failed to start the upload.',
  )
  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to start the upload.')
  }
  return response.json()
}

async function appendChunk(
  uploadId: string,
  offset: number,
  chunk: Blob,
): Promise<CuttingUploadResponse> {
  const fallback = 'Failed to upload the video.'
  const response = await uploadRequest(
    uploadUrl(uploadId),
    { method: 'PATCH', headers: { 'Upload-Offset': String(offset) }, body: chunk },
    fallback,
  )
  // 409: our offset is stale (a dropped request may still have written part
  // of its chunk) or the upload is already complete. Either way the backend's
  // own status says where to carry on from.
  if (response.status === 409) {
    return getUpload(uploadId)
  }
  if (!response.ok) {
    throw await errorFromResponse(response, fallback)
  }
  return response.json()
}

async function getUpload(uploadId: string): Promise<CuttingUploadResponse> {
  const upload = await findUpload(uploadId)
  if (upload === null) {
    throw new Error('The upload was removed from the server. Start it again.')
  }
  return upload
}

// `null` when the backend no longer has `uploadId` (e.g. it was consumed by a
// job that has since succeeded and discarded its source).
async function findUpload(uploadId: string): Promise<CuttingUploadResponse | null> {
  const fallback = 'Failed to check the upload.'
  const response = await uploadRequest(uploadUrl(uploadId), {}, fallback)
  if (response.status === 404) return null
  if (!response.ok) {
    throw await errorFromResponse(response, fallback)
  }
  return response.json()
}

// Which upload id each file went to, so uploading the same file again — after
// giving up on a dropped connection, or after a page reload — continues that
// upload rather than sending every byte again. Kept in localStorage, with an
// in-memory copy for when storage is unavailable (private window, blocked
// site data); losing it only costs a fresh upload, never correctness, since
// the backend's own status is always re-read before resuming.
const REMEMBERED_UPLOAD_PREFIX = 'cutting-upload:'
const rememberedInMemory = new Map<string, string>()

function rememberedUploadKey(file: File, target: SourceVideoTarget): string {
  return (
    REMEMBERED_UPLOAD_PREFIX +
    JSON.stringify([
      target.testId.trim().toUpperCase(),
      target.camera,
      file.name,
      file.size,
      file.lastModified,
    ])
  )
}

function recallUpload(key: string): string | null {
  try {
    const stored = localStorage.getItem(key)
    if (stored !== null) return stored
  } catch {
    // Storage unavailable: fall back to this page's own memory.
  }
  return rememberedInMemory.get(key) ?? null
}

function rememberUpload(key: string, uploadId: string): void {
  rememberedInMemory.set(key, uploadId)
  try {
    localStorage.setItem(key, uploadId)
  } catch {
    // Storage unavailable: the in-memory copy still covers this page.
  }
}

// Stop resuming `file`'s upload for `target`: call once a job has been
// created from it, so a later re-cut uploads the file afresh instead of
// pointing a second job at the first one's source.
export function forgetSourceVideoUpload(file: File, target: SourceVideoTarget): void {
  const key = rememberedUploadKey(file, target)
  rememberedInMemory.delete(key)
  try {
    localStorage.removeItem(key)
  } catch {
    // Storage unavailable: nothing stored there to remove.
  }
}

// The backend's upload for `file`, continuing a remembered one when the
// backend still has it for exactly this file, else a new one.
async function resumeOrStartUpload(
  file: File,
  target: SourceVideoTarget,
): Promise<CuttingUploadResponse> {
  const key = rememberedUploadKey(file, target)
  const rememberedId = recallUpload(key)
  if (rememberedId !== null) {
    const existing = await findUpload(rememberedId)
    if (
      existing !== null &&
      existing.camera === target.camera &&
      existing.filename === file.name &&
      existing.total_size_bytes === file.size
    ) {
      return existing
    }
  }
  const upload = await startUpload(file, target)
  rememberUpload(key, upload.upload_id)
  return upload
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// Uploads `file` as `target`'s source video and resolves to its upload id,
// once the backend has every byte. A dropped connection is resumed from the
// byte count the backend reports, never restarted from zero.
export async function uploadSourceVideo(
  file: File,
  target: SourceVideoTarget,
  options: UploadSourceVideoOptions = {},
): Promise<string> {
  const chunkSize = options.chunkSizeBytes ?? DEFAULT_UPLOAD_CHUNK_SIZE_BYTES
  const retryDelays = options.retryDelaysMs ?? DEFAULT_UPLOAD_RETRY_DELAYS_MS

  let upload: CuttingUploadResponse | null = null
  let failedAttempts = 0
  for (;;) {
    try {
      if (upload === null) {
        upload = await resumeOrStartUpload(file, target)
      } else if (failedAttempts > 0) {
        // After a failure, the backend may hold more than we last saw: part
        // of a dropped chunk can still have been written.
        upload = await getUpload(upload.upload_id)
      }
      options.onProgress?.(upload.received_bytes, upload.total_size_bytes)
      if (upload.complete) return upload.upload_id

      const offset = upload.received_bytes
      upload = await appendChunk(upload.upload_id, offset, file.slice(offset, offset + chunkSize))
      failedAttempts = 0
    } catch (err) {
      if (!(err instanceof TransientUploadError) || failedAttempts >= retryDelays.length) {
        throw err
      }
      await wait(retryDelays[failedAttempts]!)
      failedAttempts++
    }
  }
}

// Mirrors the backend's CuttingJobStatus / CuttingJobOutputStatus enums
// (backend/app/models/cutting_job.py).
export type CuttingJobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type CuttingJobOutputStatus = 'pending' | 'succeeded' | 'failed'

export const CUTTING_JOB_STATUS_LABELS: Record<CuttingJobStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

// Same limit as the backend's MAX_TESTS_PER_BATCH (app/services/cutting_jobs.py),
// which enforces it regardless; this only stops the form from offering more.
export const MAX_TESTS_PER_BATCH = 5

// One expected Cut: a (camera, condition, phase) the job will produce.
export interface CuttingJobOutput {
  camera: Camera
  condition: string
  phase: string
  status: CuttingJobOutputStatus
  failureReason: string | null
}

export interface CuttingJob {
  id: number
  testId: string
  requestedByIdentity: string | null
  status: CuttingJobStatus
  referenceCamera: Camera
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  outputs: CuttingJobOutput[]
}

interface CuttingJobOutputResponse {
  camera: Camera
  condition: string
  phase: string
  status: CuttingJobOutputStatus
  failure_reason: string | null
}

interface CuttingJobResponse {
  id: number
  test_id: string
  requested_by_identity: string | null
  status: CuttingJobStatus
  reference_camera: Camera
  created_at: string
  started_at: string | null
  finished_at: string | null
  outputs: CuttingJobOutputResponse[]
}

function toCuttingJob(row: CuttingJobResponse): CuttingJob {
  return {
    id: row.id,
    testId: row.test_id,
    requestedByIdentity: row.requested_by_identity,
    status: row.status,
    referenceCamera: row.reference_camera,
    createdAt: row.created_at,
    startedAt: row.started_at,
    finishedAt: row.finished_at,
    outputs: row.outputs.map((output) => ({
      camera: output.camera,
      condition: output.condition,
      phase: output.phase,
      status: output.status,
      failureReason: output.failure_reason,
    })),
  }
}

export interface CuttingJobBatchEntry {
  testId: string
  c1UploadId: string | null
  c2UploadId: string | null
}

// Ticket #96's marker on a "Cuts already exist" rejection (the backend's
// CUTS_ALREADY_EXIST_CODE), as opposed to every other per-Test error.
export const CUTS_ALREADY_EXIST_CODE = 'cuts_already_exist'

// Why one Test got no job. `code` is CUTS_ALREADY_EXIST_CODE when the Test
// already has Cuts and the submission didn't confirm overwriting them;
// `detail` is always safe to show as-is.
export interface CuttingJobBatchError {
  detail: string
  code: string | null
}

// Exactly one of `job`/`error` is set. `testId` is the backend's normalized
// form (`513` comes back as `T513`).
export interface CuttingJobBatchResult {
  testId: string
  job: CuttingJob | null
  error: CuttingJobBatchError | null
}

interface CuttingJobBatchResponse {
  results: {
    test_id: string
    job: CuttingJobResponse | null
    error: { status_code: number; detail: string; code: string | null } | null
  }[]
}

// Creates one independent CuttingJob per Test (up to MAX_TESTS_PER_BATCH),
// all read from the one timestamp workbook. Resolves with one result per
// entry, in the same order, as soon as the batch was accepted — a Test failing
// its own validation is a result, not a thrown error. Throws only when the
// batch as a whole was refused (too many Tests, the same Test twice, the
// workbook too large, throttled), with the backend's own message.
export async function submitCuttingJobBatch(
  excel: File,
  entries: CuttingJobBatchEntry[],
): Promise<CuttingJobBatchResult[]> {
  const formData = new FormData()
  formData.append('excel', excel)
  formData.append(
    'tests',
    JSON.stringify(
      entries.map((entry) => ({
        test_id: entry.testId,
        c1_upload_id: entry.c1UploadId,
        c2_upload_id: entry.c2UploadId,
      })),
    ),
  )

  const response = await fetch(`${API_BASE_URL}/cutting-jobs/batch`, {
    method: 'POST',
    body: formData,
  })
  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to submit the cutting jobs.')
  }

  const body: CuttingJobBatchResponse = await response.json()
  return body.results.map((result) => ({
    testId: result.test_id,
    job: result.job === null ? null : toCuttingJob(result.job),
    error: result.error === null ? null : { detail: result.error.detail, code: result.error.code },
  }))
}

export async function getCuttingJob(id: number): Promise<CuttingJob> {
  const response = await fetch(`${API_BASE_URL}/cutting-jobs/${id}`)
  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to load the cutting job.')
  }
  return toCuttingJob(await response.json())
}
