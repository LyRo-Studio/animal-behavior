<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  CUTTING_JOB_STATUS_LABELS,
  cutsDone,
  listCuttingJobs,
  type CuttingJob,
} from '@/services/cuttingJobs'
import { formatDate } from '@/utils/date'

// Ticket #101: every cutting job, whoever ran it, newest first — shared
// like AnalysesHistoryView.vue since ticket #72 (CONTEXT.md records this
// over #101's "own past cutting jobs" wording).

const jobs = ref<CuttingJob[]>([])
const isLoading = ref(true)
const loadError = ref<string | null>(null)

onMounted(async () => {
  try {
    jobs.value = await listCuttingJobs()
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Failed to load cutting jobs.'
  } finally {
    isLoading.value = false
  }
})
</script>

<template>
  <main class="min-h-screen bg-background font-sans text-foreground">
    <header class="flex items-center justify-between border-b border-border bg-surface px-6 py-4">
      <h1 class="font-serif text-xl text-primary">Cutting history</h1>
      <RouterLink
        :to="{ name: 'cutting-upload' }"
        class="text-sm font-medium text-primary hover:underline"
      >
        Back to Video Cutting
      </RouterLink>
    </header>

    <section class="mx-auto max-w-2xl px-6 py-10">
      <p v-if="isLoading" class="text-sm text-muted">Loading…</p>
      <p v-else-if="loadError" class="text-sm text-danger" role="alert">{{ loadError }}</p>
      <p v-else-if="jobs.length === 0" class="text-sm text-muted">No cutting jobs yet.</p>
      <ul v-else class="divide-y divide-border rounded-md border border-border">
        <li
          v-for="job in jobs"
          :key="job.id"
          data-testid="cutting-history-row"
          class="flex items-center justify-between px-4 py-3 text-sm"
        >
          <div>
            <RouterLink
              :to="{ name: 'cutting-job-detail', params: { id: job.id } }"
              data-testid="cutting-history-link"
              class="font-medium text-primary hover:underline"
            >
              Test {{ job.testId }}
            </RouterLink>
            <p class="mt-1 text-muted">{{ cutsDone(job) }}/{{ job.outputs.length }} Cuts</p>
          </div>
          <span class="text-muted">
            <span data-testid="cutting-history-status">
              {{ CUTTING_JOB_STATUS_LABELS[job.status] }} · {{ formatDate(job.createdAt) }}
            </span>
            <span v-if="job.requestedByIdentity" data-testid="cutting-history-requested-by">
              · {{ job.requestedByIdentity }}
            </span>
          </span>
        </li>
      </ul>
    </section>
  </main>
</template>
