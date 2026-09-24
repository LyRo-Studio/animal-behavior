import { API_BASE_URL, apiUrl, errorFromResponse } from '@/services/apiBase'

// Mirrors the backend's AnalysisJobStatus / AnalysisJobVideoStatus enums
// (backend/app/models/analysis_job.py) — issue #44's job state machine.
export type AnalysisJobStatus =
  'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed' | 'cancelled'

export type AnalysisJobVideoStatus = 'pending' | 'processing' | 'succeeded' | 'failed'

export interface AnalysisJobVideo {
  cutKey: string
  position: number
  status: AnalysisJobVideoStatus
  failureReason: string | null
}

export interface AnalysisJob {
  id: number
  // Every Test this job touches, in submission order (ticket #89 — replaces
  // the old single `testId`). Today's flow only ever submits one.
  testIds: string[]
  // Who ran it, as forwarded by Mechatronics (ticket #72) — null when no
  // identity was present at the time (e.g. local development).
  requestedByIdentity: string | null
  status: AnalysisJobStatus
  dogtraceVersion: string | null
  reportAvailable: boolean
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  videos: AnalysisJobVideo[]
}

interface AnalysisJobVideoResponse {
  cut_key: string
  position: number
  status: AnalysisJobVideoStatus
  failure_reason: string | null
}

interface AnalysisJobResponse {
  id: number
  test_ids: string[]
  requested_by_identity: string | null
  status: AnalysisJobStatus
  dogtrace_version: string | null
  report_available: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
  videos: AnalysisJobVideoResponse[]
}

function toAnalysisJob(row: AnalysisJobResponse): AnalysisJob {
  return {
    id: row.id,
    testIds: row.test_ids,
    requestedByIdentity: row.requested_by_identity,
    status: row.status,
    dogtraceVersion: row.dogtrace_version,
    reportAvailable: row.report_available,
    createdAt: row.created_at,
    startedAt: row.started_at,
    finishedAt: row.finished_at,
    videos: row.videos.map((video) => ({
      cutKey: video.cut_key,
      position: video.position,
      status: video.status,
      failureReason: video.failure_reason,
    })),
  }
}

// Mirrors the backend's MAX_TESTS_PER_JOB (backend/app/services/
// analyses.py) — CONTEXT.md's Feature B limit. The backend enforces it
// regardless (400); this only lets the view stop an 11th Test being
// selected in the first place.
export const MAX_TESTS_PER_ANALYSIS = 10

// Ticket #45's POST /analyses rejects the whole request (400) with a
// user-safe `detail` string when any selected Cut isn't valid C2 analysis
// input — surfaced here as-is rather than a generic message, via the
// shared errorFromResponse ("backend already gives a specific, safe
// reason"). Shared by createAnalysis/createWholesaleAnalysis below, the two
// shapes ticket #89's unified `{test_ids, cuts}` request allows.
async function postAnalysis(body: { test_ids: string[]; cuts?: string[] }): Promise<AnalysisJob> {
  const response = await fetch(`${API_BASE_URL}/analyses`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to start analysis.')
  }

  return toAnalysisJob(await response.json())
}

// One Test, hand-picked Cuts — today's single-Test flow, unchanged in
// meaning.
export async function createAnalysis(testId: string, cutKeys: string[]): Promise<AnalysisJob> {
  return postAnalysis({ test_ids: [testId], cuts: cutKeys })
}

// Issue #90's "Analyze selected Tests": every C2-eligible Cut of each listed
// Test, derived server-side (CONTEXT.md's "Wholesale") — `cuts` is omitted
// entirely, never sent alongside more than one Test (the backend rejects
// that with a 400). A separate function rather than an optional `cutKeys`
// on createAnalysis so that invalid combination can't be expressed here.
export async function createWholesaleAnalysis(testIds: string[]): Promise<AnalysisJob> {
  return postAnalysis({ test_ids: testIds })
}

// Thrown by getAnalysis/cancelAnalysis specifically for "no such job" (backend
// 404) — kept distinct from a generic failure, same reasoning as
// TestNotFoundError in mediaBrowser.ts. Not thrown by downloadAnalysisReport
// below: its 404 has a second, different cause with its own backend-provided
// message (see that function).
export class AnalysisNotFoundError extends Error {}

// Shared by getAnalysis/cancelAnalysis, whose 404 always means the same
// thing ("no such job") — downloadAnalysisReport
// below does its own request/response handling instead, since its 404
// doesn't share that single meaning.
async function requestAnalysisJob(
  url: string,
  init: RequestInit,
  fallback: string,
): Promise<AnalysisJob> {
  const response = await fetch(url, init)

  if (response.status === 404) {
    throw new AnalysisNotFoundError('Analysis not found.')
  }
  if (!response.ok) {
    throw await errorFromResponse(response, fallback)
  }

  return toAnalysisJob(await response.json())
}

// Ticket #52: polled by AnalysisView while a job is queued/running.
export async function getAnalysis(id: number): Promise<AnalysisJob> {
  return requestAnalysisJob(`${API_BASE_URL}/analyses/${id}`, {}, 'Failed to load analysis.')
}

// Ticket #46's POST /analyses/{id}/cancel rejects (409) with a user-safe
// `detail` string ("Only a queued analysis can be cancelled.") when the job
// isn't queued anymore — surfaced as-is via errorFromResponse (inside
// requestAnalysisJob), same as createAnalysis's 400 above; no dedicated
// error class needed since the view just shows it inline, same treatment as
// any other action error.
export async function cancelAnalysis(id: number): Promise<AnalysisJob> {
  return requestAnalysisJob(
    `${API_BASE_URL}/analyses/${id}/cancel`,
    { method: 'POST' },
    'Failed to cancel analysis.',
  )
}

// Ticket #53: every analysis, newest first (the backend caps how many) —
// history is fully shared since ticket #72, whoever ran each one. Backs both
// the global AnalysesHistoryView and MediaBrowserView's inline "previous
// analyses for this Test" panel via the optional `testId` filter.
export async function listAnalyses(testId?: string): Promise<AnalysisJob[]> {
  const url = apiUrl('/analyses')
  if (testId) url.searchParams.set('test_id', testId)

  const response = await fetch(url)

  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to load analyses.')
  }

  const rows: AnalysisJobResponse[] = await response.json()
  return rows.map(toAnalysisJob)
}

// Ticket #49's GET /analyses/{id}/report is a plain download (CONTEXT.md's
// "Analysis report download" decision) — unlike mediaBrowser.ts's
// media-token-gated streaming, there's no native <a>/<video> request
// involved, so this fetches the file directly and hands back a Blob for the
// caller to save via a synthetic download link.
export async function downloadAnalysisReport(id: number): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/analyses/${id}/report`)

  if (!response.ok) {
    // Unlike getAnalysis/cancelAnalysis, this endpoint's 404 has two
    // distinct causes with two distinct backend-provided messages
    // (backend/app/api/analyses.py's download_analysis_report): "Analysis
    // not found" for an unknown job id, vs "Report not
    // found" for a report_available job whose S3 object went missing
    // regardless. The backend already picked the right one — surfaced as-is
    // rather than collapsed into a single hardcoded message here.
    throw await errorFromResponse(response, 'Failed to download report.')
  }

  return response.blob()
}
