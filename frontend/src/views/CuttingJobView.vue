<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { CUTTING_JOB_STATUS_LABELS, getCuttingJob, type CuttingJob } from '@/services/cuttingJobs'
import { formatDate } from '@/utils/date'

// Ticket #100: where the upload flow links each created job. Deliberately a
// one-shot snapshot — live per-phase progress (polling), history and the
// re-cut confirmation are ticket #101's.

const route = useRoute()
const jobId = Number(route.params.id)

const job = ref<CuttingJob | null>(null)
const loadError = ref<string | null>(null)

const cutsDone = computed(
  () => job.value?.outputs.filter((output) => output.status === 'succeeded').length ?? 0,
)

onMounted(async () => {
  try {
    job.value = await getCuttingJob(jobId)
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : 'Failed to load the cutting job.'
  }
})
</script>

<template>
  <main class="min-h-screen bg-background font-sans text-foreground">
    <header class="flex items-center justify-between border-b border-border bg-surface px-6 py-4">
      <h1 class="font-serif text-xl text-primary">Cutting job #{{ jobId }}</h1>
      <RouterLink
        :to="{ name: 'cutting-upload' }"
        class="text-sm font-medium text-primary hover:underline"
      >
        Back to Video Cutting
      </RouterLink>
    </header>

    <section class="mx-auto max-w-2xl px-6 py-10">
      <p v-if="loadError" class="text-sm text-danger" role="alert">{{ loadError }}</p>

      <dl v-else-if="job" class="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
        <dt class="text-muted">Test</dt>
        <dd class="text-foreground">{{ job.testId }}</dd>
        <dt class="text-muted">Status</dt>
        <dd class="text-foreground" data-testid="job-status">
          {{ CUTTING_JOB_STATUS_LABELS[job.status] }}
        </dd>
        <dt class="text-muted">Cuts</dt>
        <dd class="text-foreground" data-testid="cuts-done">
          {{ cutsDone }} of {{ job.outputs.length }} done
        </dd>
        <dt class="text-muted">Submitted</dt>
        <dd class="text-foreground">{{ formatDate(job.createdAt) }}</dd>
        <template v-if="job.requestedByIdentity">
          <dt class="text-muted">By</dt>
          <dd class="text-foreground">{{ job.requestedByIdentity }}</dd>
        </template>
      </dl>

      <p v-else class="text-sm text-muted">Loading…</p>
    </section>
  </main>
</template>
