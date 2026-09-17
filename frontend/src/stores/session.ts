import { ref } from 'vue'

import {
  fetchCurrentAccount,
  login as loginRequest,
  logout as logoutRequest,
  refreshTokens,
  type AuthTokens,
  type CurrentAccount,
} from '@/services/auth'

// Bearer tokens, not cookies (docs/adr/0001-jwt-access-refresh-tokens.md) —
// persisted to localStorage so a session survives a page reload rather
// than only living in memory.
const ACCESS_TOKEN_KEY = 'animal-behavior.accessToken'
const REFRESH_TOKEN_KEY = 'animal-behavior.refreshToken'

function readStoredToken(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    // A private-browsing mode or blocked storage shouldn't crash the app —
    // it just means no session survives a reload.
    return null
  }
}

function writeStoredTokens(tokens: AuthTokens | null): void {
  try {
    if (tokens) {
      localStorage.setItem(ACCESS_TOKEN_KEY, tokens.accessToken)
      localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refreshToken)
    } else {
      localStorage.removeItem(ACCESS_TOKEN_KEY)
      localStorage.removeItem(REFRESH_TOKEN_KEY)
    }
  } catch {
    // Ignored — see readStoredToken.
  }
}

const accessToken = ref<string | null>(readStoredToken(ACCESS_TOKEN_KEY))
const refreshToken = ref<string | null>(readStoredToken(REFRESH_TOKEN_KEY))
const currentAccount = ref<CurrentAccount | null>(null)

function setTokens(tokens: AuthTokens | null): void {
  accessToken.value = tokens?.accessToken ?? null
  refreshToken.value = tokens?.refreshToken ?? null
  writeStoredTokens(tokens)
}

async function login(email: string, password: string): Promise<void> {
  const tokens = await loginRequest(email, password)
  setTokens(tokens)
  currentAccount.value = await fetchCurrentAccount(tokens.accessToken)
}

async function logout(): Promise<void> {
  if (refreshToken.value) {
    // A failed revocation call shouldn't stop the client from clearing its
    // own session — the user still expects "logout" to have worked locally.
    await logoutRequest(refreshToken.value).catch(() => undefined)
  }
  setTokens(null)
  currentAccount.value = null
}

// Shared by ensureSession and refreshAccessToken below — both end up needing
// "rotate via the refresh token, or give up and clear the session" once
// they've already decided the current access token can't be trusted as-is.
async function rotateViaRefreshToken(): Promise<boolean> {
  if (!refreshToken.value) {
    setTokens(null)
    return false
  }

  try {
    const tokens = await refreshTokens(refreshToken.value)
    setTokens(tokens)
    currentAccount.value = await fetchCurrentAccount(tokens.accessToken)
    return true
  } catch {
    setTokens(null)
    currentAccount.value = null
    return false
  }
}

/**
 * Ensure `currentAccount` reflects a valid session, refreshing the access
 * token once via the refresh token if it's stale. Returns false (and clears
 * the session) when no valid session can be established — the router
 * guard's cue to redirect to Login.
 */
async function ensureSession(): Promise<boolean> {
  if (currentAccount.value) {
    return true
  }

  if (!accessToken.value) {
    return false
  }

  try {
    currentAccount.value = await fetchCurrentAccount(accessToken.value)
    return true
  } catch {
    // Access token might just be expired — fall through to rotating it via
    // the refresh token before giving up on the session entirely.
  }

  return rotateViaRefreshToken()
}

/**
 * Rotate the access token via the refresh token on demand, for a caller that
 * just got a 401 on its own in-flight request (e.g. AnalysisView's poll)
 * rather than at route-navigation time. ensureSession() can't be reused for
 * this: it short-circuits to `true` whenever `currentAccount` is already
 * cached from an earlier login/navigation, so it would never actually
 * re-validate or rotate an access token that's gone stale mid-session.
 */
async function refreshAccessToken(): Promise<boolean> {
  return rotateViaRefreshToken()
}

export const session = {
  accessToken,
  currentAccount,
  login,
  logout,
  ensureSession,
  refreshAccessToken,
}
