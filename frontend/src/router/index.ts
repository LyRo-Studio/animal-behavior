import { createRouter, createWebHistory } from 'vue-router'

import AdminView from '@/views/AdminView.vue'
import ForgotPasswordView from '@/views/ForgotPasswordView.vue'
import HomeView from '@/views/HomeView.vue'
import LoginView from '@/views/LoginView.vue'
import SetPasswordView from '@/views/SetPasswordView.vue'
import StatusView from '@/views/StatusView.vue'
import { session } from '@/stores/session'

declare module 'vue-router' {
  interface RouteMeta {
    // Ticket #4: the Admin page requires role = admin, on top of the
    // ordinary "requires a session" check every non-public route gets.
    requiresAdmin?: boolean
  }
}

// Routes reachable without a session — every other route, including the
// pre-existing `status` health-check page, requires one (see the
// navigation guard below). `set-password` and `forgot-password` are also
// public: a newly invited or password-resetting User has no session yet.
const PUBLIC_ROUTE_NAMES = new Set(['login', 'set-password', 'forgot-password'])

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/login', name: 'login', component: LoginView },
    { path: '/forgot-password', name: 'forgot-password', component: ForgotPasswordView },
    { path: '/set-password', name: 'set-password', component: SetPasswordView },
    { path: '/status', name: 'status', component: StatusView },
    { path: '/', name: 'home', component: HomeView },
    { path: '/admin', name: 'admin', component: AdminView, meta: { requiresAdmin: true } },
  ],
})

router.beforeEach(async (to) => {
  if (PUBLIC_ROUTE_NAMES.has(to.name as string)) {
    return true
  }

  const hasSession = await session.ensureSession()
  if (!hasSession) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  // A User hitting /admin directly is redirected to Home rather than
  // shown an error — same "not reachable by a User" rule as the nav link
  // being hidden from them (CONTEXT.md's "Admin page" entry).
  if (to.meta.requiresAdmin && session.currentAccount.value?.role !== 'admin') {
    return { name: 'home' }
  }

  return true
})

export default router
