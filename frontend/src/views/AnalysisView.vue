<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { usePolledJob } from '@/composables/usePolledJob'
import {
  AnalysisNotFoundError,
  cancelAnalysis,
  downloadAnalysisReport,
  getAnalysis,
  listAnalysisTraces,
  type AnalysisJob,
  type AnalysisVideoTraces,
} from '@/services/analyses'
import { triggerBrowserDownload } from '@/utils/download'
import { breakdownByTest, hasTestBreakdown } from '@/utils/testBreakdown'

const route = useRoute()
const analysisId = computed(() => Number(route.params.id))

// Frequent enough that "2/5 videos, current: ..." feels live (issue #52's
// acceptance criteria) without hammering the backend.
const {
  job,
  isLoading,
  loadError,
  notFound,
  replace: showJob,
} = usePolledJob<AnalysisJob>({
  id: () => analysisId.value,
  load: getAnalysis,
  isActive: (current) => current.status === 'queued' || current.status === 'running',
  isNotFound: (err) => err instanceof AnalysisNotFoundError,
  fallbackError: 'Failed to load analysis.',
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
const testBreakdown = computed(() => (job.value ? breakdownByTest(job.value) : []))

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
    // The job just left {queued, running} for good: no more polling, and a
    // poll still in flight from before this resolved mustn't overwrite it.
    showJob(await cancelAnalysis(job.value.id))
  } catch (err) {
    cancelError.value = err instanceof Error ? err.message : 'Failed to cancel analysis.'
  } finally {
    isCancelling.value = false
  }
}

const isDownloading = ref(false)
const downloadError = ref<string | null>(null)

// A cancel/download error and the trace images belong to the analysis they
// were about.
watch(analysisId, () => {
  cancelError.value = null
  downloadError.value = null
  traces.value = null
  tracesError.value = null
  openTraceVideos.value = new Set()
})

// Issue #133: each video's trace images. Listed once the job has a report
// (the backend lists S3 for this, so never on every poll), and only
// rendered — so only requested — for a video someone has expanded.
const traces = ref<AnalysisVideoTraces[] | null>(null)
const tracesError = ref<string | null>(null)
const openTraceVideos = ref(new Set<string>())

const videosWithTraces = computed(
  () => traces.value?.filter((entry) => entry.images.length > 0) ?? [],
)

// The id of the shown job once it has a report, else null — stays the same
// across polls that change nothing, so the list is loaded once.
const reportReadyId = computed(() => (job.value?.reportAvailable ? job.value.id : null))

watch(
  reportReadyId,
  async (id) => {
    if (id === null) return
    try {
      const loaded = await listAnalysisTraces(id)
      if (reportReadyId.value === id) traces.value = loaded
    } catch (err) {
      if (reportReadyId.value === id) {
        tracesError.value = err instanceof Error ? err.message : 'Failed to load trace images.'
      }
    }
  },
  { immediate: true },
)

function onTraceToggle(cutKey: string, event: Event) {
  const next = new Set(openTraceVideos.value)
  if ((event.target as HTMLDetailsElement).open) next.add(cutKey)
  else next.delete(cutKey)
  openTraceVideos.value = next
}

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
          v-if="hasTestBreakdown(job)"
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

        <section v-if="job.reportAvailable" class="mt-8" data-testid="trace-images">
          <h3 class="text-base font-medium text-foreground">Trace images</h3>
          <p v-if="tracesError" class="mt-2 text-sm text-danger" role="alert">{{ tracesError }}</p>
          <p v-else-if="traces === null" class="mt-2 text-sm text-muted">Loading…</p>
          <p v-else-if="videosWithTraces.length === 0" class="mt-2 text-sm text-muted">
            No trace images for this analysis.
          </p>
          <details
            v-for="entry in videosWithTraces"
            :key="entry.cutKey"
            data-testid="trace-video"
            class="mt-2 rounded-md border border-border bg-surface"
            @toggle="onTraceToggle(entry.cutKey, $event)"
          >
            <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-foreground">
              {{ filename(entry.cutKey) }}
              <span class="font-normal text-muted">
                ({{ entry.images.length }} {{ entry.images.length === 1 ? 'image' : 'images' }})
              </span>
            </summary>
            <ul
              v-if="openTraceVideos.has(entry.cutKey)"
              class="grid grid-cols-2 gap-3 p-3 sm:grid-cols-3"
            >
              <li v-for="image in entry.images" :key="image.url">
                <a :href="image.url" target="_blank" rel="noopener">
                  <img
                    :src="image.url"
                    :alt="image.filename"
                    loading="lazy"
                    class="aspect-video w-full rounded border border-border bg-background object-contain"
                  />
                </a>
                <div class="mt-1 flex items-center justify-between gap-2 text-xs">
                  <span class="truncate text-muted" :title="image.filename">
                    {{ image.label }}
                  </span>
                  <a
                    :href="image.downloadUrl"
                    download
                    class="shrink-0 font-medium text-primary hover:underline"
                  >
                    Download
                  </a>
                </div>
              </li>
            </ul>
          </details>
        </section>
      </template>
    </section>
  </main>
</template>
