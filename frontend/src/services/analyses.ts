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
