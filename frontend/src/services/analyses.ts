import { API_BASE_URL, authHeaders, errorFromResponse } from '@/services/apiBase'

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
  testId: string
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
  test_id: string
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
    testId: row.test_id,
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

// Ticket #45's POST /analyses rejects the whole request (400) with a
// user-safe `detail` string when any selected Cut isn't valid C2 analysis
// input — surfaced here as-is rather than a generic message, via the
// shared errorFromResponse (same "backend already gives a specific, safe
// reason" call as admin.ts).
export async function createAnalysis(
  accessToken: string,
  testId: string,
  cutKeys: string[],
): Promise<AnalysisJob> {
  const response = await fetch(`${API_BASE_URL}/analyses`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(accessToken) },
    body: JSON.stringify({ test_id: testId, cuts: cutKeys }),
  })

  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to start analysis.')
  }

  return toAnalysisJob(await response.json())
}

// Thrown by getAnalysis/cancelAnalysis specifically for "no such job, or not
// this Account's" (backend 404 — the two are deliberately indistinguishable,
// see AnalysisJobNotFoundError's docstring) — kept distinct from a generic
// failure, same reasoning as TestNotFoundError in mediaBrowser.ts. Not
// thrown by downloadAnalysisReport below: its 404 has a second, different
// cause with its own backend-provided message (see that function).
export class AnalysisNotFoundError extends Error {}

// Shared by getAnalysis/cancelAnalysis, whose 404 always means the same
// thing ("no such job, or not this Account's") — downloadAnalysisReport
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
export async function getAnalysis(accessToken: string, id: number): Promise<AnalysisJob> {
  return requestAnalysisJob(
    `${API_BASE_URL}/analyses/${id}`,
    { headers: authHeaders(accessToken) },
    'Failed to load analysis.',
  )
}

// Ticket #46's POST /analyses/{id}/cancel rejects (409) with a user-safe
// `detail` string ("Only a queued analysis can be cancelled.") when the job
// isn't queued anymore — surfaced as-is via errorFromResponse (inside
// requestAnalysisJob), same as createAnalysis's 400 above; no dedicated
// error class needed since the view just shows it inline, same treatment as
// any other action error.
export async function cancelAnalysis(accessToken: string, id: number): Promise<AnalysisJob> {
  return requestAnalysisJob(
    `${API_BASE_URL}/analyses/${id}/cancel`,
    { method: 'POST', headers: authHeaders(accessToken) },
    'Failed to cancel analysis.',
  )
}

// Ticket #49's GET /analyses/{id}/report is a plain bearer-authenticated
// download (CONTEXT.md's "Analysis report download" decision) — unlike
// mediaBrowser.ts's media-token-authenticated streaming, there's no native
// <a>/<video> request involved, so this fetches the file directly (with the
// normal Authorization header) and hands back a Blob for the caller to save
// via a synthetic download link.
export async function downloadAnalysisReport(accessToken: string, id: number): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/analyses/${id}/report`, {
    headers: authHeaders(accessToken),
  })

  if (!response.ok) {
    // Unlike getAnalysis/cancelAnalysis, this endpoint's 404 has two
    // distinct causes with two distinct backend-provided messages
    // (backend/app/api/analyses.py's download_analysis_report): "Analysis
    // not found" for an unknown/another Account's job id, vs "Report not
    // found" for a report_available job whose S3 object went missing
    // regardless. The backend already picked the right one — surfaced as-is
    // rather than collapsed into a single hardcoded message here.
    throw await errorFromResponse(response, 'Failed to download report.')
  }

  return response.blob()
}
