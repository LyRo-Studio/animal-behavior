<script setup lang="ts">
import { ref } from 'vue'

import ConsolidationStatusBadge from '@/components/ConsolidationStatusBadge.vue'
import {
  createConsolidation,
  downloadConsolidation,
  type Consolidation,
} from '@/services/consolidations'
import { formatDate } from '@/utils/date'
import { triggerBlobDownload } from '@/utils/download'

// Plain file picker, no drag-and-drop (issue #113's UX decision — matches
// the mockup's `[ Select file ]`).
const selectedFile = ref<File | null>(null)

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFile.value = input.files?.[0] ?? null
  result.value = null
  submitError.value = null
}

const isSubmitting = ref(false)
const submitError = ref<string | null>(null)
const result = ref<Consolidation | null>(null)

async function consolidate() {
  if (!selectedFile.value || isSubmitting.value) return

  isSubmitting.value = true
  submitError.value = null
  result.value = null
  try {
    // Synchronous, in-request processing (issue #113: "simplest reliable
    // architecture", no worker/queue) — this resolves only once the
    // consolidation has actually finished, one way or another.
    result.value = await createConsolidation(selectedFile.value)
  } catch (err) {
    submitError.value = err instanceof Error ? err.message : 'Failed to consolidate file.'
  } finally {
    isSubmitting.value = false
  }
}

const isDownloading = ref(false)
const downloadError = ref<string | null>(null)

async function download() {
  if (!result.value || isDownloading.value) return

  isDownloading.value = true
  downloadError.value = null
  try {
    const blob = await downloadConsolidation(result.value.id)
    triggerBlobDownload(blob, result.value.originalFilename)
  } catch (err) {
    downloadError.value = err instanceof Error ? err.message : 'Failed to download file.'
  } finally {
    isDownloading.value = false
  }
}
</script>

<template>
  <main class="min-h-screen bg-background font-sans text-foreground">
    <header class="flex items-center justify-between border-b border-border bg-surface px-6 py-4">
      <h1 class="font-serif text-xl text-primary">Data Consolidation</h1>
      <div class="flex items-center gap-4">
        <RouterLink
          :to="{ name: 'consolidations-history' }"
          data-testid="consolidation-history-link"
          class="text-sm font-medium text-primary hover:underline"
        >
          History
        </RouterLink>
        <RouterLink :to="{ name: 'home' }" class="text-sm font-medium text-primary hover:underline">
          Back to Home
        </RouterLink>
      </div>
    </header>

    <section class="mx-auto max-w-2xl px-6 py-10">
      <div>
        <label for="consolidation-file" class="block text-sm font-medium text-foreground">
          Upload Excel file
        </label>
        <input
          id="consolidation-file"
          type="file"
          accept=".xlsx"
          data-testid="file-input"
          :disabled="isSubmitting"
          class="mt-2 block w-full text-sm text-foreground file:mr-4 file:rounded-md file:border-0 file:bg-primary file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-primary-hover"
          @change="onFileChange"
        />
        <p v-if="selectedFile" class="mt-2 text-sm text-muted" data-testid="selected-filename">
          {{ selectedFile.name }}
        </p>
        <p class="mt-2 text-sm text-muted">
          The result workbook holds every level: with owner (ME), without owner (ZE) and combined.
        </p>
      </div>

      <button
        type="button"
        data-testid="consolidate"
        :disabled="!selectedFile || isSubmitting"
        class="mt-6 rounded-md bg-primary px-4 py-2 font-medium text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
        @click="consolidate"
      >
        {{ isSubmitting ? 'Consolidating…' : 'Consolidate' }}
      </button>

      <p v-if="isSubmitting" class="mt-4 text-sm text-muted" data-testid="status-processing">
        Status: Processing…
      </p>

      <p v-if="submitError" class="mt-4 text-sm text-danger" role="alert">{{ submitError }}</p>

      <div
        v-if="result && result.status === 'completed'"
        class="mt-6 rounded-md border border-border bg-surface p-4"
        data-testid="consolidation-completed"
      >
        <h2 class="font-medium text-foreground">Consolidation completed</h2>
        <p class="mt-2 text-sm text-muted">
          Source: <span class="text-foreground">{{ result.originalFilename }}</span>
        </p>
        <p class="mt-1 text-sm text-muted">
          Created: <span class="text-foreground">{{ formatDate(result.createdAt) }}</span>
        </p>
        <button
          type="button"
          data-testid="download-consolidation"
          :disabled="isDownloading"
          class="mt-4 rounded-md bg-primary px-4 py-2 font-medium text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
          @click="download"
        >
          {{ isDownloading ? 'Downloading…' : 'Download consolidated file' }}
        </button>
        <p v-if="downloadError" class="mt-2 text-sm text-danger" role="alert">
          {{ downloadError }}
        </p>
      </div>

      <div
        v-else-if="result && result.status === 'failed'"
        class="mt-6 rounded-md border border-border bg-surface p-4"
        data-testid="consolidation-failed"
      >
        <h2 class="font-medium text-danger">Consolidation failed</h2>
        <p class="mt-2 text-sm text-danger">{{ result.failureReason }}</p>
      </div>

      <!-- Not returned by today's synchronous POST, but a valid status all the
           same (ticket #122): shown as reported, with nothing to download. -->
      <div
        v-else-if="result && result.status === 'processing'"
        class="mt-6 rounded-md border border-border bg-surface p-4"
        data-testid="consolidation-processing"
      >
        <ConsolidationStatusBadge :status="result.status" />
        <p class="mt-2 text-sm text-muted">
          Source: <span class="text-foreground">{{ result.originalFilename }}</span>
        </p>
        <p class="mt-1 text-sm text-muted">
          Created: <span class="text-foreground">{{ formatDate(result.createdAt) }}</span>
        </p>
      </div>
    </section>
  </main>
</template>
