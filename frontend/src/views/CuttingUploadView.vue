<script setup lang="ts">
import { computed, ref } from 'vue'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import {
  CAMERAS,
  CUTS_ALREADY_EXIST_CODE,
  forgetSourceVideoUpload,
  MAX_TESTS_PER_BATCH,
  submitCuttingJobBatch,
  uploadSourceVideo,
  type Camera,
  type CuttingJob,
  type CuttingJobBatchError,
  type CuttingJobBatchResult,
} from '@/services/cuttingJobs'

// Ticket #100, part of issue #93's Feature C: select up to 5 Tests, each
// with a C1 and/or C2 source video, plus the one timestamp workbook holding
// their rows (#97's batch endpoint reads every Test from the same file).

interface SourceVideo {
  file: File | null
  // Set once the file is fully on the backend, so a later submit (after
  // another Test's failure) doesn't upload it again.
  uploadId: string | null
  // Percent confirmed by the backend, while an upload is under way.
  progress: number | null
}

interface TestEntry {
  key: number
  testId: string
  videos: Record<Camera, SourceVideo>
  error: string | null
  // Once set, the Test is done: it's locked and left out of later submits.
  job: CuttingJob | null
  // The backend refused it because it already has Cuts (#96): the re-cut
  // dialog is asking whether to overwrite them (ticket #101).
  awaitingRecutConfirmation: boolean
  // The researcher confirmed overwriting its Cuts: every submit sends it with
  // `confirm_overwrite` from now on, so a failed follow-up (e.g. throttled)
  // is retried without asking again.
  recutConfirmed: boolean
}

let nextKey = 0

function emptyVideo(): SourceVideo {
  return { file: null, uploadId: null, progress: null }
}

function newEntry(): TestEntry {
  return {
    key: nextKey++,
    testId: '',
    videos: { C1: emptyVideo(), C2: emptyVideo() },
    error: null,
    job: null,
    awaitingRecutConfirmation: false,
    recutConfirmed: false,
  }
}

// The Test ID as submitted: what was typed, minus stray whitespace.
function testIdOf(entry: TestEntry): string {
  return entry.testId.trim()
}

const entries = ref<TestEntry[]>([newEntry()])
const excel = ref<File | null>(null)

function addTest() {
  if (entries.value.length < MAX_TESTS_PER_BATCH) entries.value.push(newEntry())
}

function removeTest(entry: TestEntry) {
  entries.value = entries.value.filter((other) => other.key !== entry.key)
}

function chosenFile(event: Event): File | null {
  return (event.target as HTMLInputElement).files?.[0] ?? null
}

// `assist`'s own filename rule (a source video starts with its Test ID and
// contains `_C1_`/`_C2_`), so the usual filename already names its Test.
const TEST_ID_FROM_FILENAME = /^(.+?)_C[12]_/i

function onVideoChange(entry: TestEntry, camera: Camera, event: Event) {
  const file = chosenFile(event)
  entry.videos[camera] = { ...emptyVideo(), file }
  const fromFilename = file?.name.match(TEST_ID_FROM_FILENAME)?.[1]
  if (fromFilename && testIdOf(entry) === '') entry.testId = fromFilename
}

// An upload is declared for one Test ID, so a new ID needs new uploads, and
// a re-cut confirmed for the old ID doesn't carry over.
function onTestIdInput(entry: TestEntry) {
  for (const camera of CAMERAS) entry.videos[camera].uploadId = null
  entry.recutConfirmed = false
}

function onExcelChange(event: Event) {
  excel.value = chosenFile(event)
}

function chosenCameras(entry: TestEntry): Camera[] {
  return CAMERAS.filter((camera) => entry.videos[camera].file !== null)
}

const pendingEntries = computed(() => entries.value.filter((entry) => entry.job === null))

const isSubmitting = ref(false)
const batchError = ref<string | null>(null)

const canSubmit = computed(
  () =>
    !isSubmitting.value &&
    excel.value !== null &&
    pendingEntries.value.length > 0 &&
    pendingEntries.value.every(
      (entry) => testIdOf(entry) !== '' && chosenCameras(entry).length > 0,
    ),
)

// Uploads whichever of `entry`'s videos aren't on the backend yet, one at a
// time. Records the first failure on the entry; `false` when there was one.
async function uploadVideos(entry: TestEntry): Promise<boolean> {
  for (const camera of chosenCameras(entry)) {
    const video = entry.videos[camera]
    if (video.uploadId !== null) continue
    video.progress = 0
    try {
      video.uploadId = await uploadSourceVideo(
        video.file!,
        { testId: testIdOf(entry), camera },
        {
          onProgress: (received, total) => {
            video.progress = total > 0 ? Math.floor((received / total) * 100) : 100
          },
        },
      )
    } catch (err) {
      entry.error = `${camera}: ${err instanceof Error ? err.message : 'Upload failed.'}`
      return false
    } finally {
      video.progress = null
    }
  }
  return true
}

// Submits `uploaded` as one batch and records each Test's outcome on it. A
// Test that already has Cuts waits for the re-cut dialog instead of showing
// an error.
async function submitBatch(uploaded: TestEntry[]) {
  const results = await submitCuttingJobBatch(
    excel.value!,
    uploaded.map((entry) => ({
      testId: testIdOf(entry),
      c1UploadId: entry.videos.C1.uploadId,
      c2UploadId: entry.videos.C2.uploadId,
      ...(entry.recutConfirmed ? { confirmOverwrite: true } : {}),
    })),
  )
  // One result per submitted Test, in submission order (#97).
  results.forEach((result, index) => applyResult(uploaded[index]!, result))
}

function applyResult(entry: TestEntry, result: CuttingJobBatchResult) {
  if (result.job === null) {
    if (result.error?.code === CUTS_ALREADY_EXIST_CODE) {
      entry.awaitingRecutConfirmation = true
    } else {
      entry.error = describeRejection(result.error)
    }
    return
  }
  entry.job = result.job
  for (const camera of chosenCameras(entry)) {
    forgetSourceVideoUpload(entry.videos[camera].file!, { testId: testIdOf(entry), camera })
  }
}

function describeRejection(error: CuttingJobBatchError | null): string {
  return error?.detail ?? 'The cutting job could not be created.'
}

// Runs one submission, holding the form while it's under way and showing a
// refusal of the whole batch under it.
async function whileSubmitting(work: () => Promise<void>) {
  isSubmitting.value = true
  batchError.value = null
  try {
    await work()
  } catch (err) {
    batchError.value = err instanceof Error ? err.message : 'Failed to submit the cutting jobs.'
  } finally {
    isSubmitting.value = false
  }
}

// Uploads first, strictly one video at a time, then one batch for every
// Test whose uploads all finished. A Test that fails (its upload, or the
// backend's validation of it) keeps its error and stays in the form, so the
// next submit retries just those Tests, resuming any half-finished upload.
async function submit() {
  if (!canSubmit.value) return

  await whileSubmitting(async () => {
    const uploaded: TestEntry[] = []
    for (const entry of pendingEntries.value) {
      entry.error = null
      if (await uploadVideos(entry)) uploaded.push(entry)
    }
    if (uploaded.length > 0) await submitBatch(uploaded)
  })
}

// Ticket #101: every Test the last submit found already cut, confirmed or
// cancelled together in one dialog.
const recutEntries = computed(() =>
  entries.value.filter((entry) => entry.awaitingRecutConfirmation),
)
const recutTestsLabel = computed(() => recutEntries.value.map(testIdOf).join(', '))

// Resubmits just those Tests with `confirm_overwrite`. Their uploads weren't
// consumed by the refused attempt (#96), so nothing is uploaded again.
async function confirmRecut() {
  const confirmed = recutEntries.value
  for (const entry of confirmed) {
    entry.awaitingRecutConfirmation = false
    entry.recutConfirmed = true
  }
  await whileSubmitting(() => submitBatch(confirmed))
}

function cancelRecut() {
  for (const entry of recutEntries.value) {
    entry.awaitingRecutConfirmation = false
    entry.error = `${testIdOf(entry)} already has Cuts; it wasn't re-cut.`
  }
}
</script>

<template>
  <main class="min-h-screen bg-background font-sans text-foreground">
    <header class="flex items-center justify-between border-b border-border bg-surface px-6 py-4">
      <h1 class="font-serif text-xl text-primary">Video Cutting</h1>
      <div class="flex items-center gap-4">
        <RouterLink
          :to="{ name: 'cutting-jobs-history' }"
          class="text-sm font-medium text-primary hover:underline"
        >
          Cutting history
        </RouterLink>
        <RouterLink :to="{ name: 'home' }" class="text-sm font-medium text-primary hover:underline">
          Back to Home
        </RouterLink>
      </div>
    </header>

    <!-- `inert` while the re-cut dialog is open, so nothing behind it can be
         reached from the keyboard either. -->
    <section class="mx-auto max-w-3xl px-6 py-10" :inert="recutEntries.length > 0 || undefined">
      <p class="text-sm text-muted">
        Upload the source video(s) of up to {{ MAX_TESTS_PER_BATCH }} Tests, plus the timestamp
        Excel holding their rows. Each Test becomes its own cutting job.
      </p>

      <div class="mt-6">
        <label for="cutting-excel" class="block text-sm font-medium text-foreground">
          Timestamp Excel
        </label>
        <input
          id="cutting-excel"
          type="file"
          accept=".xlsx"
          data-testid="excel-input"
          class="mt-2 block w-full text-sm text-foreground file:mr-4 file:rounded-md file:border-0 file:bg-primary file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-primary-hover"
          :disabled="isSubmitting"
          @change="onExcelChange"
        />
      </div>

      <ol class="mt-6 space-y-4">
        <li
          v-for="(entry, index) in entries"
          :key="entry.key"
          data-testid="test-entry"
          class="rounded-md border border-border bg-surface p-4"
        >
          <div class="flex items-end justify-between gap-4">
            <div class="flex-1">
              <label
                :for="`test-id-${entry.key}`"
                class="block text-sm font-medium text-foreground"
              >
                Test {{ index + 1 }} — Test ID
              </label>
              <input
                :id="`test-id-${entry.key}`"
                v-model="entry.testId"
                type="text"
                maxlength="50"
                placeholder="e.g. T001"
                data-testid="test-id-input"
                :disabled="isSubmitting || entry.job !== null"
                class="mt-2 block w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-foreground disabled:opacity-50"
                @input="onTestIdInput(entry)"
              />
            </div>
            <button
              type="button"
              data-testid="remove-test"
              class="rounded-md border border-border px-3 py-2 text-sm font-medium text-foreground hover:bg-background disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="isSubmitting || entries.length === 1"
              @click="removeTest(entry)"
            >
              Remove
            </button>
          </div>

          <div class="mt-4 grid gap-4 sm:grid-cols-2">
            <div v-for="camera in CAMERAS" :key="camera">
              <label
                :for="`video-${entry.key}-${camera}`"
                class="block text-sm font-medium text-foreground"
              >
                {{ camera }} source video
              </label>
              <input
                :id="`video-${entry.key}-${camera}`"
                type="file"
                accept="video/*"
                :data-testid="`${camera.toLowerCase()}-input`"
                :disabled="isSubmitting || entry.job !== null"
                class="mt-2 block w-full text-sm text-foreground file:mr-4 file:rounded-md file:border-0 file:bg-primary file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-primary-hover"
                @change="onVideoChange(entry, camera, $event)"
              />
              <div
                v-if="entry.videos[camera].progress !== null"
                class="mt-2"
                :data-testid="`upload-progress-${camera}`"
              >
                <div
                  class="h-2 overflow-hidden rounded-full bg-background"
                  role="progressbar"
                  :aria-label="`${camera} upload`"
                  :aria-valuenow="entry.videos[camera].progress ?? 0"
                  aria-valuemin="0"
                  aria-valuemax="100"
                >
                  <div
                    class="h-full bg-primary"
                    :style="{ width: `${entry.videos[camera].progress}%` }"
                  />
                </div>
                <p class="mt-1 text-xs text-muted">
                  Uploading… {{ entry.videos[camera].progress }}%
                </p>
              </div>
              <p
                v-else-if="entry.videos[camera].uploadId !== null && entry.job === null"
                class="mt-2 text-xs text-success"
              >
                Uploaded
              </p>
            </div>
          </div>

          <p v-if="entry.error" class="mt-4 text-sm text-danger" role="alert">
            {{ entry.error }}
          </p>
          <p v-if="entry.job" class="mt-4 text-sm text-success">
            Cutting job created for {{ entry.job.testId }}:
            <RouterLink
              :to="{ name: 'cutting-job-detail', params: { id: entry.job.id } }"
              data-testid="job-link"
              class="font-medium text-primary hover:underline"
            >
              view job #{{ entry.job.id }}
            </RouterLink>
          </p>
        </li>
      </ol>

      <div class="mt-4 flex items-center gap-4">
        <button
          type="button"
          data-testid="add-test"
          :disabled="isSubmitting || entries.length >= MAX_TESTS_PER_BATCH"
          class="rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50"
          @click="addTest"
        >
          Add Test
        </button>
        <button
          type="button"
          data-testid="submit-batch"
          :disabled="!canSubmit"
          class="rounded-md bg-primary px-4 py-2 font-medium text-white hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-50"
          @click="submit"
        >
          {{ isSubmitting ? 'Uploading…' : 'Upload and cut' }}
        </button>
      </div>

      <p v-if="batchError" class="mt-4 text-sm text-danger" role="alert" data-testid="batch-error">
        {{ batchError }}
      </p>
    </section>

    <ConfirmDialog
      v-if="recutEntries.length > 0"
      title="Cuts already exist"
      confirm-label="Re-cut and overwrite"
      cancel-label="Keep existing Cuts"
      @confirm="confirmRecut"
      @cancel="cancelRecut"
    >
      <p>
        {{ recutTestsLabel }} already {{ recutEntries.length === 1 ? 'has' : 'have' }} Cuts.
        Re-cutting overwrites every existing Cut it produces again.
      </p>
    </ConfirmDialog>
  </main>
</template>
