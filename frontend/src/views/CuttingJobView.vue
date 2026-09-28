<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { usePolledJob } from '@/composables/usePolledJob'
import {
  CAMERAS,
  CUTTING_JOB_STATUS_LABELS,
  CuttingJobNotFoundError,
  cancelCuttingJob,
  cutsDone,
  getCuttingJob,
  type Camera,
  type CuttingJob,
  type CuttingJobOutput,
  type CuttingJobOutputStatus,
} from '@/services/cuttingJobs'
import { formatDate } from '@/utils/date'

// Ticket #101: live per-phase progress for one cutting job, polled by
// usePolledJob like AnalysisView.vue. Each CuttingJobOutput turns
// `succeeded` as the cutting-worker uploads that phase's Cut (#98); a failed
// or skipped phase is only known once the job ends.

const route = useRoute()

function isActive(current: CuttingJob): boolean {
  return current.status === 'queued' || current.status === 'running'
}

const jobId = computed(() => Number(route.params.id))

const { job, loadError, notFound, replace } = usePolledJob<CuttingJob>({
  id: () => jobId.value,
  load: getCuttingJob,
  isActive,
  isNotFound: (err) => err instanceof CuttingJobNotFoundError,
  fallbackError: 'Failed to load the cutting job.',
})

// Issue #168: a queued job can be cancelled, by anyone; the backend deletes
// its uploaded source. No confirmation, like AnalysisView's Cancel: nothing
// has run yet, so nothing is lost.
const isCancelling = ref(false)
const cancelError = ref<string | null>(null)

async function cancel() {
  if (!job.value || isCancelling.value) return

  isCancelling.value = true
  cancelError.value = null
  try {
    // The job just left {queued, running} for good: no more polling, and a
    // poll still in flight from before this resolved mustn't overwrite it.
    replace(await cancelCuttingJob(job.value.id))
  } catch (err) {
    cancelError.value = err instanceof Error ? err.message : 'Failed to cancel the cutting job.'
  } finally {
    isCancelling.value = false
  }
}

// A cancel error belongs to the job it was about.
watch(jobId, () => {
  cancelError.value = null
})

const OUTPUT_STATUS_LABELS: Record<CuttingJobOutputStatus, string> = {
  pending: 'Pending',
  succeeded: 'Done',
  failed: 'Failed',
}

const OUTPUT_STATUS_CLASSES: Record<CuttingJobOutputStatus, string> = {
  pending: 'text-muted',
  succeeded: 'text-success',
  failed: 'text-danger',
}

const progressPercent = computed(() => {
  const total = job.value?.outputs.length ?? 0
  return total === 0 ? 0 : Math.round((cutsDone(job.value!) / total) * 100)
})

const cameras = computed<Camera[]>(() =>
  CAMERAS.filter((camera) => job.value?.outputs.some((output) => output.camera === camera)),
)

interface PhaseRow {
  label: string
  byCamera: Partial<Record<Camera, CuttingJobOutput>>
}

// One row per (condition, phase), in the backend's output order (ME F1–F8,
// then ZE F1–F7), with each camera's output side by side.
const phaseRows = computed<PhaseRow[]>(() => {
  const rows = new Map<string, PhaseRow>()
  for (const output of job.value?.outputs ?? []) {
    const label = `${output.condition} ${output.phase}`
    const row = rows.get(label) ?? { label, byCamera: {} }
    row.byCamera[output.camera] = output
    rows.set(label, row)
  }
  return [...rows.values()]
})

const failedOutputs = computed(
  () => job.value?.outputs.filter((output) => output.status === 'failed') ?? [],
)
</script>

<template>
  <main class="min-h-screen bg-background font-sans text-foreground">
    <header class="flex items-center justify-between border-b border-border bg-surface px-6 py-4">
      <h1 class="font-serif text-xl text-primary">Cutting job #{{ route.params.id }}</h1>
      <div class="flex items-center gap-4">
        <RouterLink
          :to="{ name: 'cutting-jobs-history' }"
          class="text-sm font-medium text-primary hover:underline"
        >
          Cutting history
        </RouterLink>
        <RouterLink
          :to="{ name: 'cutting-upload' }"
          class="text-sm font-medium text-primary hover:underline"
        >
          Back to Video Cutting
        </RouterLink>
      </div>
    </header>

    <section class="mx-auto max-w-2xl px-6 py-10">
      <p v-if="notFound" class="text-sm text-danger" role="alert">Cutting job not found.</p>

      <template v-else-if="job">
        <dl class="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
          <dt class="text-muted">Test</dt>
          <dd class="text-foreground">{{ job.testId }}</dd>
          <dt class="text-muted">Status</dt>
          <dd class="text-foreground" data-testid="job-status">
            {{ CUTTING_JOB_STATUS_LABELS[job.status] }}
          </dd>
          <!-- A cancelled job never ran a phase (issue #168). -->
          <template v-if="job.status !== 'cancelled'">
            <dt class="text-muted">Cuts</dt>
            <dd class="text-foreground" data-testid="cuts-done">
              {{ cutsDone(job) }} of {{ job.outputs.length }} done
            </dd>
          </template>
          <dt class="text-muted">Submitted</dt>
          <dd class="text-foreground">{{ formatDate(job.createdAt) }}</dd>
          <template v-if="job.finishedAt">
            <dt class="text-muted">Finished</dt>
            <dd class="text-foreground">{{ formatDate(job.finishedAt) }}</dd>
          </template>
          <template v-if="job.requestedByIdentity">
            <dt class="text-muted">By</dt>
            <dd class="text-foreground">{{ job.requestedByIdentity }}</dd>
          </template>
        </dl>

        <p v-if="loadError" class="mt-4 text-sm text-danger" role="alert">{{ loadError }}</p>

        <div v-if="job.status === 'queued'" class="mt-6">
          <button
            type="button"
            data-testid="cancel"
            :disabled="isCancelling"
            class="rounded-md border border-border px-4 py-2 font-medium text-danger hover:bg-background disabled:cursor-not-allowed disabled:opacity-50"
            @click="cancel"
          >
            {{ isCancelling ? 'Cancelling…' : 'Cancel' }}
          </button>
        </div>
        <p
          v-if="cancelError"
          data-testid="cancel-error"
          class="mt-4 text-sm text-danger"
          role="alert"
        >
          {{ cancelError }}
        </p>

        <div
          v-if="isActive(job)"
          class="mt-6 h-2 w-full overflow-hidden rounded-full bg-border"
          role="progressbar"
          aria-label="Cuts done"
          :aria-valuenow="progressPercent"
          aria-valuemin="0"
          aria-valuemax="100"
        >
          <div
            class="h-full rounded-full bg-primary transition-all"
            :style="{ width: `${progressPercent}%` }"
          />
        </div>

        <table v-if="job.status !== 'cancelled'" class="mt-6 w-full text-left text-sm">
          <caption class="sr-only">
            Cuts per phase
          </caption>
          <thead>
            <tr class="border-b border-border text-muted">
              <th scope="col" class="py-2 pr-4 font-medium">Phase</th>
              <th v-for="camera in cameras" :key="camera" scope="col" class="py-2 pr-4 font-medium">
                {{ camera }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in phaseRows"
              :key="row.label"
              data-testid="phase-row"
              class="border-b border-border"
            >
              <th scope="row" class="py-2 pr-4 font-medium text-foreground">{{ row.label }}</th>
              <td
                v-for="camera in cameras"
                :key="camera"
                class="py-2 pr-4"
                :class="row.byCamera[camera] && OUTPUT_STATUS_CLASSES[row.byCamera[camera].status]"
              >
                {{ row.byCamera[camera] ? OUTPUT_STATUS_LABELS[row.byCamera[camera].status] : '—' }}
              </td>
            </tr>
          </tbody>
        </table>

        <ul v-if="failedOutputs.length > 0" data-testid="failed-outputs" class="mt-6 space-y-2">
          <li
            v-for="output in failedOutputs"
            :key="`${output.camera}-${output.condition}-${output.phase}`"
            class="rounded-md border border-border bg-surface p-3 text-sm"
          >
            <p class="font-medium text-foreground">
              {{ output.camera }} {{ output.condition }} {{ output.phase }}
            </p>
            <p v-if="output.failureReason" class="text-danger">{{ output.failureReason }}</p>
          </li>
        </ul>
      </template>

      <p v-else-if="loadError" class="text-sm text-danger" role="alert">{{ loadError }}</p>
      <p v-else class="text-sm text-muted">Loading…</p>
    </section>
  </main>
</template>
