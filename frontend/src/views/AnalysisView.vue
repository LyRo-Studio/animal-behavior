<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  AnalysisNotFoundError,
  cancelAnalysis,
  downloadAnalysisReport,
  getAnalysis,
  type AnalysisJob,
} from '@/services/analyses'
import { triggerBrowserDownload } from '@/utils/download'
import { breakdownByTest, hasTestBreakdown } from '@/utils/testBreakdown'

const route = useRoute()
const analysisId = Number(route.params.id)

const job = ref<AnalysisJob | null>(null)
const isLoading = ref(true)
const loadError = ref<string | null>(null)
const notFound = ref(false)

// Frequent enough that "2/5 videos, current: ..." feels live (issue #52's
// acceptance criteria) without hammering the backend — same shape as
// StatusView.vue's own setTimeout-based poll/retry loop.
const POLL_INTERVAL_MS = 2000
let pollTimer: ReturnType<typeof setTimeout> | undefined

// Bumped whenever a *newer* request should make any earlier one's eventual
// resolution irrelevant: the component unmounting mid-fetch (an in-flight
// loadJob() otherwise has no timer yet for onUnmounted to clear, and would
// schedule an uncancellable next poll once it resolves), or cancel()
// succeeding while a poll happens to be in flight (which would otherwise
// overwrite the just-cancelled job with a stale queued/running snapshot and
// revive polling). Same purpose as MediaBrowserView.vue's
// analysisToken/searchToken guards against out-of-order responses.
let requestGeneration = 0

async function loadJob() {
  const generation = requestGeneration

  try {
    const result = await getAnalysis(analysisId)
    if (generation !== requestGeneration) return
    job.value = result
    loadError.value = null
    // Only reschedule while there's still something to watch for — a
    // terminal or cancelled job never changes again, so polling it forever
    // would just be wasted requests.
    if (result.status === 'queued' || result.status === 'running') {
      pollTimer = setTimeout(loadJob, POLL_INTERVAL_MS)
    }
  } catch (err) {
    if (generation !== requestGeneration) return
    if (err instanceof AnalysisNotFoundError) {
      // Non-transient — there is no such job id, so there's nothing to
      // retry (unlike the generic-error branch below).
      notFound.value = true
    } else {
      loadError.value = err instanceof Error ? err.message : 'Failed to load analysis.'
      pollTimer = setTimeout(loadJob, POLL_INTERVAL_MS)
    }
  } finally {
    if (generation === requestGeneration) {
      isLoading.value = false
    }
  }
}

onMounted(() => {
  if (Number.isNaN(analysisId)) {
    notFound.value = true
    isLoading.value = false
    return
  }
  loadJob()
})

onUnmounted(() => {
  requestGeneration++
  clearTimeout(pollTimer)
})

// "Processed" (not just succeeded) so the counter reads as "how far
// through the batch we are" while running, matching the ticket's own
// "2/5 videos, current: ..." example — a failed video still counts as one
// that's been attempted.
const processedCount = computed(
  () => job.value?.videos.filter((video) => video.status !== 'pending').length ?? 0,
)
const totalCount = computed(() => job.value?.videos.length ?? 0)
const progressPercent = computed(() =>
  totalCount.value === 0 ? 0 : Math.round((processedCount.value / totalCount.value) * 100),
)
// Only ever non-null under live per-video progress (ticket #48) — under
// ticket #47's coarser status-only mode this stays null throughout, and the
// template simply omits the "current: ..." fragment, per issue #52's "works
// correctly whether the backend ... [is] coarse ... or has live per-video
// updates" requirement.
const currentVideo = computed(
  () => job.value?.videos.find((video) => video.status === 'processing') ?? null,
)

const succeededCount = computed(
  () => job.value?.videos.filter((video) => video.status === 'succeeded').length ?? 0,
)
const failedVideos = computed(
  () => job.value?.videos.filter((video) => video.status === 'failed') ?? [],
)

// Issue #92 — see hasTestBreakdown for when it's shown at all.
const testBreakdown = computed(() =>
  job.value && hasTestBreakdown(job.value) ? breakdownByTest(job.value) : [],
)

function filename(cutKey: string): string {
  return cutKey.slice(cutKey.lastIndexOf('/') + 1)
}

const isCancelling = ref(false)
const cancelError = ref<string | null>(null)

async function cancel() {
  if (!job.value || isCancelling.value) return

  isCancelling.value = true
  cancelError.value = null
  try {
    job.value = await cancelAnalysis(job.value.id)
    // The job just left {queued, running} for good — no more polling
    // needed, and any poll response still in flight from before this
    // resolved must not be allowed to overwrite it (see requestGeneration).
    requestGeneration++
    clearTimeout(pollTimer)
  } catch (err) {
    cancelError.value = err instanceof Error ? err.message : 'Failed to cancel analysis.'
  } finally {
    isCancelling.value = false
  }
}

const isDownloading = ref(false)
const downloadError = ref<string | null>(null)

async function downloadReport() {
  if (!job.value || isDownloading.value) return

  isDownloading.value = true
  downloadError.value = null
  try {
    const blob = await downloadAnalysisReport(job.value.id)
    // A plain fetch (CONTEXT.md's "Analysis report download" decision),
    // unlike mediaBrowser.ts's token-in-URL Cut download — the browser
    // never requests this URL itself, so the file has to be saved via a
    // Blob object URL instead of a native href.
    const objectUrl = URL.createObjectURL(blob)
    triggerBrowserDownload(objectUrl, 'casiop_report.xlsx')
    URL.revokeObjectURL(objectUrl)
  } catch (err) {
    downloadError.value = err instanceof Error ? err.message : 'Failed to download report.'
  } finally {
    isDownloading.value = false
  }
}
</script>

<template>
  <main class="min-h-screen bg-background font-sans text-foreground">
    <header class="flex items-center justify-between border-b border-border bg-surface px-6 py-4">
      <h1 class="font-serif text-xl text-primary">Analysis</h1>
      <RouterLink :to="{ name: 'media' }" class="text-sm font-medium text-primary hover:underline">
        Back to Media Browser
      </RouterLink>
    </header>

    <section class="mx-auto max-w-2xl px-6 py-10">
      <p v-if="isLoading" class="text-sm text-muted">Loading…</p>
      <p v-else-if="notFound" class="text-sm text-danger" role="alert">Analysis not found.</p>

      <template v-else-if="job">
        <h2 class="text-lg font-medium text-foreground">Test {{ job.testIds.join(', ') }}</h2>
        <p v-if="job.requestedByIdentity" class="mt-1 text-sm text-muted">
          Run by
          <span class="font-medium text-foreground" data-testid="requested-by">{{
            job.requestedByIdentity
          }}</span>
        </p>
        <p class="mt-1 text-sm text-muted">
          Status:
          <span class="font-medium text-foreground" data-testid="status">{{ job.status }}</span>
        </p>

        <p v-if="loadError" class="mt-2 text-sm text-danger" role="alert">{{ loadError }}</p>

        <div v-if="job.status === 'queued' || job.status === 'running'" class="mt-6">
          <div class="h-2 w-full overflow-hidden rounded-full bg-border">
            <div
              class="h-full rounded-full bg-primary transition-all"
              :style="{ width: `${progressPercent}%` }"
            />
          </div>
          <p class="mt-2 text-sm text-muted" data-testid="progress-summary">
            {{ processedCount }}/{{ totalCount }} videos
            <span v-if="currentVideo">— current: {{ filename(currentVideo.cutKey) }}</span>
          </p>
        </div>

        <div v-else-if="job.status !== 'cancelled'" class="mt-6" data-testid="terminal-summary">
          <p class="text-sm text-foreground">
            {{ succeededCount }} succeeded, {{ failedVideos.length }} failed
          </p>
          <ul v-if="failedVideos.length > 0" class="mt-3 space-y-2">
            <li
              v-for="video in failedVideos"
              :key="video.cutKey"
              class="rounded-md border border-border bg-surface p-3 text-sm"
            >
              <p class="font-medium text-foreground">{{ filename(video.cutKey) }}</p>
              <p class="text-danger">{{ video.failureReason }}</p>
            </li>
          </ul>
        </div>

        <table
          v-if="testBreakdown.length > 0"
          data-testid="test-breakdown"
          class="mt-6 w-full text-left text-sm"
        >
          <caption class="sr-only">
            Videos per Test
          </caption>
          <thead>
            <tr class="border-b border-border text-muted">
              <th scope="col" class="py-2 pr-4 font-medium">Test</th>
              <th scope="col" class="py-2 pr-4 font-medium">Succeeded</th>
              <th scope="col" class="py-2 pr-4 font-medium">Failed</th>
              <th scope="col" class="py-2 font-medium">Total</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="entry in testBreakdown"
              :key="entry.testId"
              data-testid="test-breakdown-row"
              class="border-b border-border"
            >
              <th scope="row" class="py-2 pr-4 font-medium text-foreground">
                {{ entry.testId }}
              </th>
              <td class="py-2 pr-4">{{ entry.succeeded }}</td>
              <td class="py-2 pr-4" :class="entry.failed > 0 ? 'text-danger' : ''">
                {{ entry.failed }}
              </td>
              <td class="py-2">{{ entry.total }}</td>
            </tr>
          </tbody>
        </table>

        <div class="mt-6 flex items-center gap-3">
          <button
            v-if="job.status === 'queued'"
            type="button"
            data-testid="cancel"
            :disabled="isCancelling"
            class="rounded-md border border-border px-4 py-2 font-medium text-danger hover:bg-background disabled:cursor-not-allowed disabled:opacity-50"
            @click="cancel"
          >
            {{ isCancelling ? 'Cancelling…' : 'Cancel' }}
          </button>

          <button
            v-if="job.reportAvailable"
            type="button"
            data-testid="download-report"
            :disabled="isDownloading"
            class="rounded-md bg-primary px-4 py-2 font-medium text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
            @click="downloadReport"
          >
            {{ isDownloading ? 'Downloading…' : 'Download report' }}
          </button>
        </div>

        <p v-if="cancelError" class="mt-2 text-sm text-danger" role="alert">{{ cancelError }}</p>
        <p v-if="downloadError" class="mt-2 text-sm text-danger" role="alert">
          {{ downloadError }}
        </p>
      </template>
    </section>
  </main>
</template>
