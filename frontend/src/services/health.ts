export interface HealthStatus {
  status: string
}

// `||`, not `??`: an explicitly empty VITE_API_BASE_URL (e.g. an
// unpopulated build var in some CI/preview config) should also fall back
// to the default, not resolve to a same-origin relative request.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export async function fetchHealth(): Promise<HealthStatus> {
  const response = await fetch(`${API_BASE_URL}/health`)

  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`)
  }

  return response.json()
}
