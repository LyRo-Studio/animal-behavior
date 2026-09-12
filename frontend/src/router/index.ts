import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '@/views/HomeView.vue'
import LoginView from '@/views/LoginView.vue'
import StatusView from '@/views/StatusView.vue'
import { session } from '@/stores/session'

// Login is the only route reachable without a session — every other route,
// including the pre-existing `status` health-check page, requires one (see
// the navigation guard below), per ticket #3's acceptance criteria.
const PUBLIC_ROUTE_NAMES = new Set(['login'])

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/login', name: 'login', component: LoginView },
    { path: '/status', name: 'status', component: StatusView },
    { path: '/', name: 'home', component: HomeView },
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

  return true
})

export default router
