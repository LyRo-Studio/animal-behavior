<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { listAnalyses, type AnalysisJob } from '@/services/analyses'
import { formatDate } from '@/utils/date'

const analyses = ref<AnalysisJob[]>([])
const isLoading = ref(true)
const loadError = ref<string | null>(null)

async function loadAnalyses() {
  isLoading.value = true
  loadError.value = null
  try {
    // Every analysis, whoever ran it, newest first (ticket #72 made history
    // fully shared) — the backend already orders and bounds it.
    analyses.value = await listAnalyses()
  } catch {
    loadError.value = 'Failed to load analyses.'
  } finally {
    isLoading.value = false
  }
}

onMounted(loadAnalyses)
</script>

<template>
  <main class="min-h-screen bg-background font-sans text-foreground">
    <header class="flex items-center justify-between border-b border-border bg-surface px-6 py-4">
      <h1 class="font-serif text-xl text-primary">Analyses</h1>
      <RouterLink :to="{ name: 'home' }" class="text-sm font-medium text-primary hover:underline">
        Back to Home
      </RouterLink>
    </header>

    <section class="mx-auto max-w-2xl px-6 py-10">
      <p v-if="isLoading" class="text-sm text-muted">Loading…</p>
      <p v-else-if="loadError" class="text-sm text-danger" role="alert">{{ loadError }}</p>
      <p v-else-if="analyses.length === 0" class="text-sm text-muted">No analyses yet.</p>
      <ul v-else class="divide-y divide-border rounded-md border border-border">
        <li
          v-for="job in analyses"
          :key="job.id"
          class="flex items-center justify-between px-4 py-3 text-sm"
        >
          <RouterLink
            :to="{ name: 'analysis-detail', params: { id: job.id } }"
            data-testid="analysis-history-link"
            class="font-medium text-primary hover:underline"
          >
            Test {{ job.testIds.join(', ') }}
          </RouterLink>
          <span class="text-muted">
            <span data-testid="analysis-history-status">
              {{ job.status }} · {{ formatDate(job.createdAt) }}
            </span>
            <span v-if="job.requestedByIdentity" data-testid="analysis-history-requested-by">
              · {{ job.requestedByIdentity }}
            </span>
          </span>
        </li>
      </ul>
    </section>
  </main>
</template>
