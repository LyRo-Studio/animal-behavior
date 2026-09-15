<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { listCuts, listTestIds, TestNotFoundError, type Cut } from '@/services/mediaBrowser'
import { session } from '@/stores/session'

const testIds = ref<string[]>([])
const isLoadingTestIds = ref(true)
const testIdsError = ref<string | null>(null)

const query = ref('')
// Whether the suggestion dropdown may show at all — separate from
// `suggestions.length > 0` so a fresh selection can close the dropdown
// even though the now-selected id still matches its own query (e.g.
// typing "t01" and picking "T014" leaves query="T014", which still
// self-matches and would otherwise leave the dropdown open on just that
// one entry). Reopens as soon as the user edits the query again.
const suggestionsOpen = ref(false)
const selectedTestId = ref<string | null>(null)
const cuts = ref<Cut[] | null>(null)
const isLoadingCuts = ref(false)
const cutsError = ref<string | null>(null)
const notFound = ref(false)

// A Test id always starts with "T"/"t" followed only by digits
// (CONTEXT.md's "Test" term, e.g. "T001") — anything else (e.g. "hallo")
// can never be typed into the search box in the first place, not even as
// an intermediate keystroke.
function sanitizeTestIdInput(value: string): string {
  let result = ''
  for (let i = 0; i < value.length; i++) {
    const char = value[i]!
    if (i === 0 ? /[Tt]/.test(char) : /[0-9]/.test(char)) {
      result += char
    }
  }
  return result
}

function handleQueryInput(event: Event) {
  const target = event.target as HTMLInputElement
  const sanitized = sanitizeTestIdInput(target.value)
  query.value = sanitized
  // Force the DOM back in sync even when the sanitized value equals the
  // previous one (e.g. typing "hallo" leaves query unchanged at "") — Vue
  // skips re-patching :value in that case since nothing reactively
  // changed, which would otherwise leave the rejected text visible in the
  // box despite the app already treating it as empty.
  target.value = sanitized
  suggestionsOpen.value = true
}

// Filtered client-side from the once-fetched full list as the user types
// (CONTEXT.md's "Media browser — Test discovery" decision) — capped so a
// broad query (e.g. "T") doesn't render all ~450 Tests at once.
const suggestions = computed(() => {
  const needle = query.value.trim().toUpperCase()
  if (!needle || !suggestionsOpen.value) return []
  return testIds.value.filter((id) => id.toUpperCase().includes(needle)).slice(0, 20)
})

async function loadTestIds() {
  isLoadingTestIds.value = true
  testIdsError.value = null
  try {
    if (!session.accessToken.value) return
    testIds.value = await listTestIds(session.accessToken.value)
  } catch {
    testIdsError.value = 'Failed to load Tests.'
  } finally {
    isLoadingTestIds.value = false
  }
}

// Guards against a slower, earlier search's response overwriting a faster,
// later one's — e.g. selecting T001 then quickly T002 before T001's
// request resolves must never leave T002's header paired with T001's Cuts.
let searchToken = 0

// Same shape sanitizeTestIdInput enforces while typing — guards a
// programmatic search() call too (e.g. a bare "T" submitted via Enter
// before any digits are typed) rather than sending an obviously-incomplete
// id to the backend.
const COMPLETE_TEST_ID_RE = /^T\d+$/

async function search(testId: string) {
  // Uppercased to match the suggestion list's case-insensitive filtering —
  // otherwise typing "t001" and pressing Enter (instead of picking the
  // suggestion) would 404 against the backend's case-sensitive Test id
  // pattern even though the Test exists.
  const trimmed = testId.trim().toUpperCase()
  suggestionsOpen.value = false
  if (!COMPLETE_TEST_ID_RE.test(trimmed) || !session.accessToken.value) return

  const currentToken = ++searchToken
  selectedTestId.value = trimmed
  cuts.value = null
  notFound.value = false
  cutsError.value = null
  isLoadingCuts.value = true

  try {
    const result = await listCuts(session.accessToken.value, trimmed)
    if (currentToken !== searchToken) return
    cuts.value = result
  } catch (err) {
    if (currentToken !== searchToken) return
    if (err instanceof TestNotFoundError) {
      notFound.value = true
    } else {
      cutsError.value = err instanceof Error ? err.message : 'Failed to load Cuts.'
    }
  } finally {
    if (currentToken === searchToken) {
      isLoadingCuts.value = false
    }
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
            :value="query"
            type="text"
            :disabled="isLoadingTestIds"
            placeholder="e.g. T001"
            autocomplete="off"
            class="w-full rounded-md border border-border bg-surface px-3 py-2 text-foreground focus:border-primary focus:outline-none"
            @input="handleQueryInput"
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
