<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  CONSOLIDATION_CONDITION_LABELS,
  CONSOLIDATION_DISPLAY_NAME_MAX_LENGTH,
  deleteConsolidation,
  downloadConsolidation,
  listConsolidations,
  renameConsolidation,
  type Consolidation,
} from '@/services/consolidations'
import { formatDate } from '@/utils/date'
import { triggerBlobDownload } from '@/utils/download'

const consolidations = ref<Consolidation[]>([])
const isLoading = ref(true)
const loadError = ref<string | null>(null)

async function loadConsolidations() {
  isLoading.value = true
  loadError.value = null
  try {
    // Every consolidation, whoever ran it, newest first (ticket #116: fully
    // shared, like AnalysesHistoryView) — the backend already orders and
    // bounds it.
    consolidations.value = await listConsolidations()
  } catch {
    loadError.value = 'Failed to load consolidations.'
  } finally {
    isLoading.value = false
  }
}

onMounted(loadConsolidations)

const downloadingId = ref<number | null>(null)
// Keyed by consolidation id, so an error shows on the row it happened on.
const downloadErrors = ref<Record<number, string>>({})

async function download(consolidation: Consolidation) {
  if (downloadingId.value !== null) return

  downloadingId.value = consolidation.id
  delete downloadErrors.value[consolidation.id]
  try {
    const blob = await downloadConsolidation(consolidation.id)
    triggerBlobDownload(blob, consolidation.originalFilename)
  } catch (err) {
    downloadErrors.value[consolidation.id] =
      err instanceof Error ? err.message : 'Failed to download file.'
  } finally {
    downloadingId.value = null
  }
}

// Inline rename (ticket #117) — one row at a time. The input starts from the
// current display name (empty when there is none, with the original
// filename as a placeholder), and saving it blank clears the display name.
const renamingId = ref<number | null>(null)
const renameDraft = ref('')
const isSavingRename = ref(false)
const renameError = ref<string | null>(null)

function startRename(consolidation: Consolidation) {
  renamingId.value = consolidation.id
  renameDraft.value = consolidation.displayName ?? ''
  renameError.value = null
}

function cancelRename() {
  renamingId.value = null
  renameError.value = null
}

async function saveRename(consolidation: Consolidation) {
  if (isSavingRename.value) return

  isSavingRename.value = true
  renameError.value = null
  try {
    const renamed = await renameConsolidation(consolidation.id, renameDraft.value.trim() || null)
    consolidations.value = consolidations.value.map((row) =>
      row.id === renamed.id ? renamed : row,
    )
    renamingId.value = null
  } catch (err) {
    renameError.value = err instanceof Error ? err.message : 'Failed to rename consolidation.'
  } finally {
    isSavingRename.value = false
  }
}

// Delete (ticket #118) — a hard delete with no undo, so it takes a second,
// inline confirmation click. Not offered while a consolidation is still
// `processing` (the backend refuses it: it may still be running).
const confirmingDeleteId = ref<number | null>(null)
const deletingId = ref<number | null>(null)
// Keyed by consolidation id, like downloadErrors.
const deleteErrors = ref<Record<number, string>>({})

async function confirmDelete(consolidation: Consolidation) {
  if (deletingId.value !== null) return

  deletingId.value = consolidation.id
  delete deleteErrors.value[consolidation.id]
  try {
    await deleteConsolidation(consolidation.id)
    consolidations.value = consolidations.value.filter((row) => row.id !== consolidation.id)
  } catch (err) {
    deleteErrors.value[consolidation.id] =
      err instanceof Error ? err.message : 'Failed to delete consolidation.'
  } finally {
    deletingId.value = null
    confirmingDeleteId.value = null
  }
}
</script>

<template>
  <main class="min-h-screen bg-background font-sans text-foreground">
    <header class="flex items-center justify-between border-b border-border bg-surface px-6 py-4">
      <h1 class="font-serif text-xl text-primary">Consolidation History</h1>
      <div class="flex items-center gap-4">
        <RouterLink
          :to="{ name: 'consolidation' }"
          class="text-sm font-medium text-primary hover:underline"
        >
          New consolidation
        </RouterLink>
        <RouterLink :to="{ name: 'home' }" class="text-sm font-medium text-primary hover:underline">
          Back to Home
        </RouterLink>
      </div>
    </header>

    <section class="mx-auto max-w-2xl px-6 py-10">
      <p v-if="isLoading" class="text-sm text-muted">Loading…</p>
      <p v-else-if="loadError" class="text-sm text-danger" role="alert">{{ loadError }}</p>
      <p v-else-if="consolidations.length === 0" class="text-sm text-muted">
        No consolidations yet.
      </p>
      <ul v-else class="divide-y divide-border rounded-md border border-border">
        <li
          v-for="consolidation in consolidations"
          :key="consolidation.id"
          data-testid="consolidation-history-row"
          class="px-4 py-3 text-sm"
        >
          <div class="flex items-center justify-between gap-4">
            <div class="min-w-0">
              <div v-if="renamingId === consolidation.id" class="flex items-center gap-2">
                <input
                  v-model="renameDraft"
                  type="text"
                  aria-label="Display name"
                  data-testid="consolidation-history-rename-input"
                  :maxlength="CONSOLIDATION_DISPLAY_NAME_MAX_LENGTH"
                  :placeholder="consolidation.originalFilename"
                  :disabled="isSavingRename"
                  class="min-w-0 flex-1 rounded-md border border-border bg-surface px-2 py-1 text-sm text-foreground"
                  @keydown.enter="saveRename(consolidation)"
                  @keydown.escape="cancelRename"
                />
                <button
                  type="button"
                  data-testid="consolidation-history-rename-save"
                  :disabled="isSavingRename"
                  class="font-medium text-primary hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                  @click="saveRename(consolidation)"
                >
                  Save
                </button>
                <button
                  type="button"
                  data-testid="consolidation-history-rename-cancel"
                  :disabled="isSavingRename"
                  class="text-muted hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                  @click="cancelRename"
                >
                  Cancel
                </button>
              </div>
              <div v-else class="flex items-center gap-2">
                <p
                  class="truncate font-medium text-foreground"
                  data-testid="consolidation-history-name"
                >
                  {{ consolidation.displayName ?? consolidation.originalFilename }}
                </p>
                <button
                  type="button"
                  data-testid="consolidation-history-rename"
                  class="shrink-0 text-primary hover:underline"
                  @click="startRename(consolidation)"
                >
                  Rename
                </button>
              </div>
              <p
                v-if="consolidation.displayName"
                class="truncate text-muted"
                data-testid="consolidation-history-original-filename"
              >
                Source: {{ consolidation.originalFilename }}
              </p>
              <p class="text-muted">
                <span data-testid="consolidation-history-condition">{{
                  CONSOLIDATION_CONDITION_LABELS[consolidation.condition]
                }}</span>
                ·
                <span data-testid="consolidation-history-status">
                  {{ consolidation.status }} · {{ formatDate(consolidation.createdAt) }}
                </span>
                <span
                  v-if="consolidation.requestedByIdentity"
                  data-testid="consolidation-history-requested-by"
                >
                  · {{ consolidation.requestedByIdentity }}
                </span>
              </p>
            </div>
            <div class="flex shrink-0 items-center gap-3">
              <button
                v-if="consolidation.status === 'completed'"
                type="button"
                data-testid="consolidation-history-download"
                :disabled="downloadingId !== null"
                class="rounded-md bg-primary px-3 py-1.5 font-medium text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
                @click="download(consolidation)"
              >
                {{ downloadingId === consolidation.id ? 'Downloading…' : 'Download' }}
              </button>
              <template v-if="consolidation.status !== 'processing'">
                <template v-if="confirmingDeleteId === consolidation.id">
                  <span class="text-muted">Delete permanently?</span>
                  <button
                    type="button"
                    data-testid="consolidation-history-delete-confirm"
                    :disabled="deletingId !== null"
                    class="font-medium text-danger hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                    @click="confirmDelete(consolidation)"
                  >
                    {{ deletingId === consolidation.id ? 'Deleting…' : 'Delete' }}
                  </button>
                  <button
                    type="button"
                    data-testid="consolidation-history-delete-cancel"
                    :disabled="deletingId !== null"
                    class="text-muted hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                    @click="confirmingDeleteId = null"
                  >
                    Cancel
                  </button>
                </template>
                <button
                  v-else
                  type="button"
                  data-testid="consolidation-history-delete"
                  class="text-danger hover:underline"
                  @click="confirmingDeleteId = consolidation.id"
                >
                  Delete
                </button>
              </template>
            </div>
          </div>
          <p
            v-if="consolidation.status === 'failed'"
            class="mt-1 text-danger"
            data-testid="consolidation-history-failure-reason"
          >
            {{ consolidation.failureReason }}
          </p>
          <p
            v-if="renamingId === consolidation.id && renameError"
            class="mt-1 text-danger"
            role="alert"
          >
            {{ renameError }}
          </p>
          <p v-if="downloadErrors[consolidation.id]" class="mt-1 text-danger" role="alert">
            {{ downloadErrors[consolidation.id] }}
          </p>
          <p v-if="deleteErrors[consolidation.id]" class="mt-1 text-danger" role="alert">
            {{ deleteErrors[consolidation.id] }}
          </p>
        </li>
      </ul>
    </section>
  </main>
</template>
