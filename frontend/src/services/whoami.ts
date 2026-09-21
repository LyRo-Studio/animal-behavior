import { API_BASE_URL } from '@/services/apiBase'

// Ticket #72: the app has no login of its own — Mechatronics authenticates
// the person and forwards their identity to the backend, which echoes it
// back here so Home can show who they are. `null` when there's no identity
// to show (no header configured/sent, e.g. local development).
export async function fetchIdentity(): Promise<string | null> {
  const response = await fetch(`${API_BASE_URL}/whoami`)

  if (!response.ok) {
    throw new Error(`Identity lookup failed with status ${response.status}`)
  }

  const body: { identity: string | null } = await response.json()
  return body.identity
}
