import { API_BASE_URL } from '@/services/apiBase'

/** Consume an invite/reset token (unauthenticated — no session exists yet). */
export async function setPassword(token: string, password: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/accounts/set-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, password }),
  })

  if (!response.ok) {
    throw new Error('This link is invalid or has expired.')
  }
}
