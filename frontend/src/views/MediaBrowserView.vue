<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  CutInfoNotFoundError,
  DatasetFolderNotFoundError,
  getCutMediaInfo,
  listCuts,
  listDatasetFolder,
  listTestIds,
  mediaStreamUrl,
  requestMediaToken,
  TestNotFoundError,
  type Cut,
  type CutMediaInfo,
  type DatasetEntry,
  type MediaTokenAction,
} from '@/services/mediaBrowser'
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
  // A new search's Cuts share no relationship with whatever was playing
  // (or had its info panel open) for the previous Test — never leave a
  // stale player or info panel showing.
  closePlayback()
  openInfoCutKey.value = null

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

// Ticket #21: play/download a Cut via a lazily-minted, single-Cut-scoped
// media token — never minted eagerly for a whole Test's Cuts, only on an
// actual Play/Download click. Keyed by `${key}:${action}` so a Play click
// on one Cut never disables a Download click on another (or the other
// action on the same one) while its own request is in flight.
const mediaActionInFlight = ref<Record<string, boolean>>({})
const mediaActionError = ref<string | null>(null)
const playingCutKey = ref<string | null>(null)
const playbackUrl = ref<string | null>(null)

function isMediaActionInFlight(cut: Cut, action: MediaTokenAction): boolean {
  return mediaActionInFlight.value[`${cut.key}:${action}`] === true
}

async function playCut(cut: Cut) {
  if (!session.accessToken.value || isMediaActionInFlight(cut, 'play')) return
  const flightKey = `${cut.key}:play`
  mediaActionInFlight.value[flightKey] = true
  mediaActionError.value = null

  try {
    const token = await requestMediaToken(session.accessToken.value, cut.key, 'play')
    playingCutKey.value = cut.key
    playbackUrl.value = mediaStreamUrl(cut.key, 'play', token)
  } catch (err) {
    mediaActionError.value = err instanceof Error ? err.message : 'Failed to start playback.'
  } finally {
    mediaActionInFlight.value[flightKey] = false
  }
}

function closePlayback() {
  playingCutKey.value = null
  playbackUrl.value = null
}

// Ticket #22: a Cut's probed media info (duration, resolution, codec),
// fetched lazily — only when the user opens a specific Cut's info panel,
// never eagerly for a whole Test's Cuts (same convention as
// requestMediaToken above). Fetched at most once per Cut client-side too:
// reopening an already-loaded panel just reveals the cached result rather
// than issuing another request (the backend caches server-side regardless,
// but there's no reason to round-trip again once the answer is in hand).
const cutMediaInfo = ref<Record<string, CutMediaInfo>>({})
const cutInfoInFlight = ref<Record<string, boolean>>({})
const cutInfoError = ref<Record<string, string>>({})
const openInfoCutKey = ref<string | null>(null)

function isCutInfoOpen(cut: Cut): boolean {
  return openInfoCutKey.value === cut.key
}

async function toggleCutInfo(cut: Cut) {
  if (isCutInfoOpen(cut)) {
    openInfoCutKey.value = null
    return
  }
  openInfoCutKey.value = cut.key

  if (cutMediaInfo.value[cut.key] || cutInfoInFlight.value[cut.key] || !session.accessToken.value)
    return

  cutInfoInFlight.value[cut.key] = true
  delete cutInfoError.value[cut.key]
  try {
    cutMediaInfo.value[cut.key] = await getCutMediaInfo(session.accessToken.value, cut.key)
  } catch (err) {
    cutInfoError.value[cut.key] =
      err instanceof CutInfoNotFoundError
        ? 'Cut not found.'
        : err instanceof Error
          ? err.message
          : 'Failed to load media info.'
  } finally {
    cutInfoInFlight.value[cut.key] = false
  }
}

function formatDuration(totalSeconds: number): string {
  const rounded = Math.round(totalSeconds)
  const minutes = Math.floor(rounded / 60)
  const seconds = rounded % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

async function downloadCut(cut: Cut) {
  if (!session.accessToken.value || isMediaActionInFlight(cut, 'download')) return
  const flightKey = `${cut.key}:download`
  mediaActionInFlight.value[flightKey] = true
  mediaActionError.value = null

  try {
    const token = await requestMediaToken(session.accessToken.value, cut.key, 'download')
    // A native download the browser's Content-Disposition-driven save
    // dialog handles — not a fetch, so the SPA never buffers the file.
    const link = document.createElement('a')
    link.href = mediaStreamUrl(cut.key, 'download', token)
    link.rel = 'noreferrer'
    document.body.appendChild(link)
    link.click()
    link.remove()
  } catch (err) {
    mediaActionError.value = err instanceof Error ? err.message : 'Failed to start download.'
  } finally {
    mediaActionInFlight.value[flightKey] = false
  }
}

// The current Dataset folder, as path segments relative to the Datasets
// root (e.g. ["dataset_v1", "train"]) — [] is the top level. Ticket #20:
// view/browse only, no download action for individual files or folders.
const datasetPath = ref<string[]>([])
const datasetEntries = ref<DatasetEntry[] | null>(null)
const isLoadingDatasets = ref(true)
const datasetsError = ref<string | null>(null)
const datasetNotFound = ref(false)

// Same stale-response guard as `searchToken` above — navigating into a
// folder before a slower, earlier folder's listing resolves must never
// leave that later folder's view showing the earlier one's entries.
let datasetLoadToken = 0

async function loadDatasetFolder() {
  const currentToken = ++datasetLoadToken
  isLoadingDatasets.value = true
  datasetsError.value = null
  datasetNotFound.value = false

  try {
    if (!session.accessToken.value) return
    const result = await listDatasetFolder(session.accessToken.value, datasetPath.value.join('/'))
    if (currentToken !== datasetLoadToken) return
    datasetEntries.value = result
  } catch (err) {
    if (currentToken !== datasetLoadToken) return
    datasetEntries.value = null
    if (err instanceof DatasetFolderNotFoundError) {
      datasetNotFound.value = true
    } else {
      datasetsError.value = err instanceof Error ? err.message : 'Failed to load Dataset folder.'
    }
  } finally {
    if (currentToken === datasetLoadToken) {
      isLoadingDatasets.value = false
    }
  }
}

function openDatasetFolder(name: string) {
  datasetPath.value = [...datasetPath.value, name]
  loadDatasetFolder()
}

function goToDatasetRoot() {
  datasetPath.value = []
  loadDatasetFolder()
}

// `index` is the breadcrumb segment clicked — keeps everything up to and
// including it, dropping the rest of the path.
function goToDatasetSegment(index: number) {
  datasetPath.value = datasetPath.value.slice(0, index + 1)
  loadDatasetFolder()
}

onMounted(() => {
  loadTestIds()
  loadDatasetFolder()
})
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
        <template v-else-if="cuts">
          <p v-if="mediaActionError" class="mt-2 text-sm text-danger" role="alert">
            {{ mediaActionError }}
          </p>
          <video
            v-if="playbackUrl"
            data-testid="cut-player"
            :src="playbackUrl"
            controls
            autoplay
            class="mt-4 w-full rounded-md border border-border bg-surface"
          ></video>
          <button
            v-if="playbackUrl"
            type="button"
            data-testid="close-player"
            class="mt-2 text-sm font-medium text-primary hover:underline"
            @click="closePlayback"
          >
            Close player
          </button>
          <table class="mt-4 w-full text-left text-sm">
            <thead>
              <tr class="border-b border-border text-muted">
                <th class="py-2 pr-4 font-medium">Filename</th>
                <th class="py-2 pr-4 font-medium">Camera</th>
                <th class="py-2 pr-4 font-medium">Condition</th>
                <th class="py-2 pr-4 font-medium">Phase</th>
                <th class="py-2 pr-4 font-medium">Size</th>
                <th class="py-2 pr-4 font-medium">Last modified</th>
                <th class="py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="cut in cuts" :key="cut.key">
                <tr class="border-b border-border">
                  <td class="py-2 pr-4">{{ cut.filename }}</td>
                  <td class="py-2 pr-4">{{ cut.camera ?? '—' }}</td>
                  <td class="py-2 pr-4">{{ cut.condition ?? '—' }}</td>
                  <td class="py-2 pr-4">{{ cut.phase ?? '—' }}</td>
                  <td class="py-2 pr-4">{{ formatSize(cut.size) }}</td>
                  <td class="py-2 pr-4">{{ formatDate(cut.lastModified) }}</td>
                  <td class="py-2">
                    <div class="flex gap-3">
                      <button
                        type="button"
                        data-testid="play-cut"
                        class="font-medium text-primary hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                        :disabled="isMediaActionInFlight(cut, 'play')"
                        @click="playCut(cut)"
                      >
                        Play
                      </button>
                      <button
                        type="button"
                        data-testid="download-cut"
                        class="font-medium text-primary hover:underline disabled:cursor-not-allowed disabled:opacity-50"
                        :disabled="isMediaActionInFlight(cut, 'download')"
                        @click="downloadCut(cut)"
                      >
                        Download
                      </button>
                      <button
                        type="button"
                        data-testid="info-cut"
                        class="font-medium text-primary hover:underline"
                        @click="toggleCutInfo(cut)"
                      >
                        {{ isCutInfoOpen(cut) ? 'Hide info' : 'Info' }}
                      </button>
                    </div>
                  </td>
                </tr>
                <tr v-if="isCutInfoOpen(cut)" class="border-b border-border">
                  <td colspan="7" class="bg-background px-2 py-3" data-testid="cut-info-panel">
                    <p v-if="cutInfoInFlight[cut.key]" class="text-sm text-muted">Loading…</p>
                    <p v-else-if="cutInfoError[cut.key]" class="text-sm text-danger" role="alert">
                      {{ cutInfoError[cut.key] }}
                    </p>
                    <dl
                      v-else-if="cutMediaInfo[cut.key]"
                      class="flex flex-wrap gap-x-8 gap-y-1 text-sm"
                    >
                      <div class="flex gap-2">
                        <dt class="font-medium text-muted">Duration</dt>
                        <dd data-testid="cut-info-duration">
                          {{ formatDuration(cutMediaInfo[cut.key]!.durationSeconds) }}
                        </dd>
                      </div>
                      <div class="flex gap-2">
                        <dt class="font-medium text-muted">Resolution</dt>
                        <dd data-testid="cut-info-resolution">
                          {{ cutMediaInfo[cut.key]!.width }}x{{ cutMediaInfo[cut.key]!.height }}
                        </dd>
                      </div>
                      <div class="flex gap-2">
                        <dt class="font-medium text-muted">Codec</dt>
                        <dd data-testid="cut-info-codec">{{ cutMediaInfo[cut.key]!.codec }}</dd>
                      </div>
                    </dl>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </template>
      </div>
    </section>

    <section class="mx-auto max-w-3xl px-6 pb-10">
      <h2 class="text-lg font-medium text-foreground">Browse Datasets</h2>

      <nav
        class="mt-2 flex flex-wrap items-center gap-1 text-sm text-muted"
        aria-label="Dataset folder path"
      >
        <button type="button" class="hover:text-primary hover:underline" @click="goToDatasetRoot">
          Datasets
        </button>
        <template v-for="(segment, index) in datasetPath" :key="index">
          <span aria-hidden="true">/</span>
          <button
            type="button"
            class="hover:text-primary hover:underline"
            @click="goToDatasetSegment(index)"
          >
            {{ segment }}
          </button>
        </template>
      </nav>

      <p v-if="isLoadingDatasets" class="mt-2 text-sm text-muted">Loading…</p>
      <p
        v-else-if="datasetNotFound"
        data-testid="dataset-not-found"
        class="mt-2 text-sm text-muted"
      >
        This folder no longer exists.
      </p>
      <p v-else-if="datasetsError" class="mt-2 text-sm text-danger" role="alert">
        {{ datasetsError }}
      </p>
      <ul
        v-else-if="datasetEntries"
        class="mt-4 divide-y divide-border rounded-md border border-border"
      >
        <li v-if="datasetEntries.length === 0" class="px-3 py-2 text-sm text-muted">
          Empty folder.
        </li>
        <li
          v-for="entry in datasetEntries"
          :key="entry.key"
          class="flex items-center gap-3 px-3 py-2 text-sm"
        >
          <span
            class="w-14 shrink-0 text-xs font-medium uppercase tracking-wide"
            :class="entry.isFolder ? 'text-primary' : 'text-muted'"
          >
            {{ entry.isFolder ? 'Folder' : 'File' }}
          </span>
          <button
            v-if="entry.isFolder"
            type="button"
            data-testid="dataset-folder"
            class="text-left font-medium text-foreground hover:text-primary hover:underline"
            @click="openDatasetFolder(entry.name)"
          >
            {{ entry.name }}
          </button>
          <span v-else data-testid="dataset-file" class="text-foreground">{{ entry.name }}</span>
          <span v-if="!entry.isFolder && entry.size !== null" class="ml-auto text-muted">
            {{ formatSize(entry.size) }}
          </span>
        </li>
      </ul>
    </section>
  </main>
</template>
