import { createRouter, createWebHistory } from 'vue-router'

import AnalysesHistoryView from '@/views/AnalysesHistoryView.vue'
import AnalysisView from '@/views/AnalysisView.vue'
import ConsolidationsHistoryView from '@/views/ConsolidationsHistoryView.vue'
import ConsolidationView from '@/views/ConsolidationView.vue'
import CuttingJobView from '@/views/CuttingJobView.vue'
import CuttingUploadView from '@/views/CuttingUploadView.vue'
import HomeView from '@/views/HomeView.vue'
import MediaBrowserView from '@/views/MediaBrowserView.vue'
import StatusView from '@/views/StatusView.vue'

// Ticket #72: no navigation guard, no login/admin routes — Mechatronics
// authenticates everyone before a request reaches the app at all
// (docs/adr/0004-trust-mega-tronics-remove-application-auth.md), so every
// route here is open to whoever got this far.
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/status', name: 'status', component: StatusView },
    { path: '/', name: 'home', component: HomeView },
    { path: '/media', name: 'media', component: MediaBrowserView },
    // Declared before the `:id` route below so it isn't shadowed by it —
    // vue-router matches static segments over dynamic ones regardless of
    // order, but keeping the static route first reads more naturally
    // alongside it.
    { path: '/analyses', name: 'analyses-history', component: AnalysesHistoryView },
    { path: '/analyses/:id', name: 'analysis-detail', component: AnalysisView },
    // Ticket #115, part of issue #113's Excel consolidation feature.
    { path: '/consolidation', name: 'consolidation', component: ConsolidationView },
    // Ticket #116: every past consolidation, fully shared like /analyses.
    {
      path: '/consolidations',
      name: 'consolidations-history',
      component: ConsolidationsHistoryView,
    },
    // Ticket #100, part of issue #93's Feature C: upload source videos and
    // submit up to 5 Tests' cutting jobs.
    { path: '/cutting', name: 'cutting-upload', component: CuttingUploadView },
    { path: '/cutting-jobs/:id', name: 'cutting-job-detail', component: CuttingJobView },
  ],
})

export default router
