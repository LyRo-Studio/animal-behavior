import { API_BASE_URL, errorFromResponse } from '@/services/apiBase'

// Mirrors the backend's ConsolidationCondition / ConsolidationStatus enums
// (backend/app/models/consolidation.py) — ticket #115, part of issue #113's
// Excel consolidation feature. ME/ZE are this app's own Condition
// vocabulary (CONTEXT.md's Language section), not consolidation/'s
// internal Dutch "deel" names.
export type ConsolidationCondition = 'ME' | 'ZE' | 'ME_ZE'
export type ConsolidationStatus = 'processing' | 'completed' | 'failed'

// How each Condition is shown to the user — the upload page's selector and
// the history list both render from this.
export const CONSOLIDATION_CONDITION_LABELS: Record<ConsolidationCondition, string> = {
  ME: 'ME',
  ZE: 'ZE',
  ME_ZE: 'ME + ZE',
}

export interface Consolidation {
  id: number
  originalFilename: string
  displayName: string | null
  condition: ConsolidationCondition
  status: ConsolidationStatus
  requestedByIdentity: string | null
  failureReason: string | null
  inputSizeBytes: number
  resultSizeBytes: number | null
  createdAt: string
  completedAt: string | null
}

interface ConsolidationResponse {
  id: number
  original_filename: string
  display_name: string | null
  condition: ConsolidationCondition
  status: ConsolidationStatus
  requested_by_identity: string | null
  failure_reason: string | null
  input_size_bytes: number
  result_size_bytes: number | null
  created_at: string
  completed_at: string | null
}

function toConsolidation(row: ConsolidationResponse): Consolidation {
  return {
    id: row.id,
    originalFilename: row.original_filename,
    displayName: row.display_name,
    condition: row.condition,
    status: row.status,
    requestedByIdentity: row.requested_by_identity,
    failureReason: row.failure_reason,
    inputSizeBytes: row.input_size_bytes,
    resultSizeBytes: row.result_size_bytes,
    createdAt: row.created_at,
    completedAt: row.completed_at,
  }
}

// Ticket #115's POST /consolidations always resolves to 201 with a
// Consolidation, whether its outcome is `completed` or `failed` — a failed
// consolidation *attempt* is a successfully handled request, not an HTTP
// error (issue #113: it still needs to show up with a clear reason). A
// thrown error here means no Consolidation was created at all (bad
// extension, empty/oversized file, or throttled) — surfaced via the
// backend's own safe `detail` string, same as createAnalysis.
export async function createConsolidation(
  file: File,
  condition: ConsolidationCondition,
): Promise<Consolidation> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('condition', condition)

  const response = await fetch(`${API_BASE_URL}/consolidations`, {
    method: 'POST',
    body: formData,
  })

  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to consolidate file.')
  }

  return toConsolidation(await response.json())
}

// Ticket #116: every consolidation, newest first (the backend caps how
// many), whoever ran it and whatever its status — `failed` rows included,
// so a failed attempt still shows up with its reason. Fully shared, same as
// listAnalyses.
export async function listConsolidations(): Promise<Consolidation[]> {
  const response = await fetch(`${API_BASE_URL}/consolidations`)

  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to load consolidations.')
  }

  const rows: ConsolidationResponse[] = await response.json()
  return rows.map(toConsolidation)
}

// Same bound as the backend's DISPLAY_NAME_MAX_LENGTH
// (backend/app/models/consolidation.py) — the backend enforces it
// regardless; this only stops the input from accepting more.
export const CONSOLIDATION_DISPLAY_NAME_MAX_LENGTH = 255

// Ticket #117: set a consolidation's display name, or clear it with `null`
// (the history list then falls back to `originalFilename`, which a rename
// never changes).
export async function renameConsolidation(
  id: number,
  displayName: string | null,
): Promise<Consolidation> {
  const response = await fetch(`${API_BASE_URL}/consolidations/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_name: displayName }),
  })

  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to rename consolidation.')
  }

  return toConsolidation(await response.json())
}

// A plain download (ticket #115, mirroring downloadAnalysisReport) — fetch
// + Blob, no native <a>/<video> request involved.
export async function downloadConsolidation(id: number): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/consolidations/${id}/download`)

  if (!response.ok) {
    throw await errorFromResponse(response, 'Failed to download consolidated file.')
  }

  return response.blob()
}
