<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { listCuts, listTestIds, TestNotFoundError, type Cut } from '@/services/mediaBrowser'
import { session } from '@/stores/session'

const testIds = ref<string[]>([])
const isLoadingTestIds = ref(true)
const testIdsError = ref<string | null>(null)

const query = ref('')
const selectedTestId = ref<string | null>(null)
const cuts = ref<Cut[] | null>(null)
const isLoadingCuts = ref(false)
const cutsError = ref<string | null>(null)
const notFound = ref(false)

// Filtered client-side from the once-fetched full list as the user types
// (CONTEXT.md's "Media browser — Test discovery" decision) — capped so a
// broad query (e.g. "T") doesn't render all ~450 Tests at once.
const suggestions = computed(() => {
  const needle = query.value.trim().toUpperCase()
  if (!needle) return []
  return testIds.value.filter((id) => id.toUpperCase().includes(needle)).slice(0, 20)
})

async function loadTestIds() {
  if (!session.accessToken.value) return

  isLoadingTestIds.value = true
  testIdsError.value = null
  try {
    testIds.value = await listTestIds(session.accessToken.value)
  } catch {
    testIdsError.value = 'Failed to load Tests.'
  } finally {
    isLoadingTestIds.value = false
  }
}

async function search(testId: string) {
  const trimmed = testId.trim()
  if (!trimmed || !session.accessToken.value) return

  selectedTestId.value = trimmed
  cuts.value = null
  notFound.value = false
  cutsError.value = null
  isLoadingCuts.value = true

  try {
    cuts.value = await listCuts(session.accessToken.value, trimmed)
  } catch (err) {
    if (err instanceof TestNotFoundError) {
      notFound.value = true
    } else {
      cutsError.value = err instanceof Error ? err.message : 'Failed to load Cuts.'
    }
  } finally {
    isLoadingCuts.value = false
  }
}

function handleSubmit() {
  search(query.value)
}

function selectSuggestion(testId: string) {
  query.value = testId
  search(testId)
}

function formatSize(bytes: number): string {
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString()
}

onMounted(loadTestIds)
</script>

<template>
  <main class="min-h-screen bg-background font-sans text-foreground">
    <header class="flex items-center justify-between border-b border-border bg-surface px-6 py-4">
      <h1 class="font-serif text-xl text-primary">Media Browser</h1>
      <RouterLink :to="{ name: 'home' }" class="text-sm font-medium text-primary hover:underline">
        Back to Home
      </RouterLink>
    </header>

    <section class="mx-auto max-w-3xl px-6 py-10">
      <h2 class="text-lg font-medium text-foreground">Search a Test</h2>
      <p v-if="testIdsError" class="mt-2 text-sm text-danger" role="alert">{{ testIdsError }}</p>

      <form class="relative mt-4 flex gap-3" @submit.prevent="handleSubmit">
        <div class="flex-1">
          <label class="sr-only" for="test-search">Test ID</label>
          <input
            id="test-search"
            v-model="query"
            type="text"
            :disabled="isLoadingTestIds"
            placeholder="e.g. T001"
            autocomplete="off"
            class="w-full rounded-md border border-border bg-surface px-3 py-2 text-foreground focus:border-primary focus:outline-none"
          />
          <ul
            v-if="suggestions.length > 0"
            data-testid="test-suggestions"
            class="absolute z-10 mt-1 w-full rounded-md border border-border bg-surface shadow-md"
          >
            <li v-for="id in suggestions" :key="id">
              <button
                type="button"
                data-testid="test-suggestion"
                class="block w-full px-3 py-2 text-left text-sm hover:bg-background"
                @click="selectSuggestion(id)"
              >
                {{ id }}
              </button>
            </li>
          </ul>
        </div>
        <button
          type="submit"
          class="rounded-md bg-primary px-4 py-2 font-medium text-white hover:bg-primary-hover"
        >
          Search
        </button>
      </form>

      <div v-if="selectedTestId" class="mt-10">
        <h2 class="text-lg font-medium text-foreground">Cuts for {{ selectedTestId }}</h2>

        <p v-if="isLoadingCuts" class="mt-2 text-sm text-muted">Loading…</p>
        <p v-else-if="notFound" data-testid="not-found" class="mt-2 text-sm text-muted">
          No Test found with ID "{{ selectedTestId }}".
        </p>
        <p v-else-if="cutsError" class="mt-2 text-sm text-danger" role="alert">{{ cutsError }}</p>
        <table v-else-if="cuts" class="mt-4 w-full text-left text-sm">
          <thead>
            <tr class="border-b border-border text-muted">
              <th class="py-2 pr-4 font-medium">Filename</th>
              <th class="py-2 pr-4 font-medium">Camera</th>
              <th class="py-2 pr-4 font-medium">Condition</th>
              <th class="py-2 pr-4 font-medium">Phase</th>
              <th class="py-2 pr-4 font-medium">Size</th>
              <th class="py-2 font-medium">Last modified</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="cut in cuts" :key="cut.key" class="border-b border-border">
              <td class="py-2 pr-4">{{ cut.filename }}</td>
              <td class="py-2 pr-4">{{ cut.camera ?? '—' }}</td>
              <td class="py-2 pr-4">{{ cut.condition ?? '—' }}</td>
              <td class="py-2 pr-4">{{ cut.phase ?? '—' }}</td>
              <td class="py-2 pr-4">{{ formatSize(cut.size) }}</td>
              <td class="py-2">{{ formatDate(cut.lastModified) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</template>
