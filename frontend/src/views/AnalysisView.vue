<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import {
  AnalysisNotFoundError,
  AnalysisUnauthorizedError,
  cancelAnalysis,
  downloadAnalysisReport,
  getAnalysis,
  type AnalysisJob,
} from '@/services/analyses'
import { session } from '@/stores/session'
import { triggerBrowserDownload } from '@/utils/download'

const route = useRoute()
const analysisId = Number(route.params.id)

const job = ref<AnalysisJob | null>(null)
const isLoading = ref(true)
const loadError = ref<string | null>(null)
const notFound = ref(false)
const sessionExpired = ref(false)

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
  if (!session.accessToken.value) return
  const generation = requestGeneration

  try {
    const result = await getAnalysis(session.accessToken.value, analysisId)
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
      // Non-transient — this Account will never be able to see this job id,
      // so there's nothing to retry (unlike the generic-error branch below).
      notFound.value = true
    } else if (err instanceof AnalysisUnauthorizedError) {
      // Non-transient in the same sense as AnalysisNotFoundError above — an
      // expired/invalid access token never becomes valid again on its own,
      // so retrying every 2s would just spam the backend with 401s forever
      // (as it did before this branch existed) while the page kept showing
      // a now-stale job snapshot alongside the error. Replacing the whole
      // view with a dedicated message (rather than layering it onto
      // loadError, which renders next to the stale status) makes the stale
      // snapshot disappear too.
      sessionExpired.value = true
      job.value = null
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

function filename(cutKey: string): string {
  return cutKey.slice(cutKey.lastIndexOf('/') + 1)
}

const isCancelling = ref(false)
const cancelError = ref<string | null>(null)

async function cancel() {
  if (!session.accessToken.value || !job.value || isCancelling.value) return

  isCancelling.value = true
  cancelError.value = null
  try {
    job.value = await cancelAnalysis(session.accessToken.value, job.value.id)
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
  if (!session.accessToken.value || !job.value || isDownloading.value) return

  isDownloading.value = true
  downloadError.value = null
  try {
    const blob = await downloadAnalysisReport(session.accessToken.value, job.value.id)
    // A plain bearer-authenticated fetch (CONTEXT.md's "Analysis report
    // download" decision), unlike mediaBrowser.ts's token-in-URL Cut
    // download — the browser never requests this URL itself, so the file
    // has to be saved via a Blob object URL instead of a native href.
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

      <div v-else-if="sessionExpired" data-testid="session-expired">
        <p class="text-sm text-danger" role="alert">
          Your session has expired. Log in again to keep watching this analysis.
        </p>
        <RouterLink
          :to="{ name: 'login', query: { redirect: route.fullPath } }"
          class="mt-3 inline-block text-sm font-medium text-primary hover:underline"
        >
          Log in again
        </RouterLink>
      </div>

      <template v-else-if="job">
        <h2 class="text-lg font-medium text-foreground">Test {{ job.testId }}</h2>
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
