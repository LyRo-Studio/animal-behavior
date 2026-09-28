<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  CUTTING_JOB_STATUS_LABELS,
  CuttingJobNotFoundError,
  getCuttingJob,
  type Camera,
  type CuttingJob,
  type CuttingJobOutput,
  type CuttingJobOutputStatus,
} from '@/services/cuttingJobs'
import { formatDate } from '@/utils/date'

// Ticket #101: live per-phase progress for one cutting job, polled the same
// way AnalysisView.vue polls an analysis. Each CuttingJobOutput turns
// `succeeded` as the cutting-worker uploads that phase's Cut (#98); a failed
// or skipped phase is only known once the job ends.

const route = useRoute()
const jobId = Number(route.params.id)

const job = ref<CuttingJob | null>(null)
const loadError = ref<string | null>(null)
const notFound = ref(false)

const POLL_INTERVAL_MS = 2000
let pollTimer: ReturnType<typeof setTimeout> | undefined
// Bumped on unmount, so a request still in flight then can't schedule
// another poll once it resolves (AnalysisView.vue's requestGeneration).
let requestGeneration = 0

function isActive(current: CuttingJob): boolean {
  return current.status === 'queued' || current.status === 'running'
}

async function loadJob() {
  const generation = requestGeneration
  try {
    const result = await getCuttingJob(jobId)
    if (generation !== requestGeneration) return
    job.value = result
    loadError.value = null
    if (isActive(result)) pollTimer = setTimeout(loadJob, POLL_INTERVAL_MS)
  } catch (err) {
    if (generation !== requestGeneration) return
    if (err instanceof CuttingJobNotFoundError) {
      notFound.value = true
      return
    }
    loadError.value = err instanceof Error ? err.message : 'Failed to load the cutting job.'
    pollTimer = setTimeout(loadJob, POLL_INTERVAL_MS)
  }
}

onMounted(() => {
  if (Number.isNaN(jobId)) {
    notFound.value = true
    return
  }
  loadJob()
})

onUnmounted(() => {
  requestGeneration++
  clearTimeout(pollTimer)
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

const cutsDone = computed(
  () => job.value?.outputs.filter((output) => output.status === 'succeeded').length ?? 0,
)
const progressPercent = computed(() => {
  const total = job.value?.outputs.length ?? 0
  return total === 0 ? 0 : Math.round((cutsDone.value / total) * 100)
})

const cameras = computed<Camera[]>(() =>
  (['C1', 'C2'] as const).filter((camera) =>
    job.value?.outputs.some((output) => output.camera === camera),
  ),
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
          <dt class="text-muted">Cuts</dt>
          <dd class="text-foreground" data-testid="cuts-done">
            {{ cutsDone }} of {{ job.outputs.length }} done
          </dd>
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

        <table class="mt-6 w-full text-left text-sm">
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
