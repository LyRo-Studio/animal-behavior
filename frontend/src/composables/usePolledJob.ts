import { onScopeDispose, ref, watch, type Ref } from 'vue'

// Shared by AnalysisView.vue and CuttingJobView.vue: load one job by id and
// keep re-fetching it while it can still change, the same setTimeout-based
// loop StatusView.vue uses.

export const POLL_INTERVAL_MS = 2000

export interface PolledJobOptions<T> {
  // The job to show, read reactively (the route's `:id`): Vue Router reuses
  // the mounted view when only `:id` changes, so a new id has to restart the
  // loop rather than keep polling the old job. NaN means "not a job id".
  id: () => number
  load: (id: number) => Promise<T>
  // A job in a terminal state never changes again, so polling it further is
  // wasted requests.
  isActive: (job: T) => boolean
  // A 404: there's no such job, so unlike any other failure nothing is retried.
  isNotFound: (err: unknown) => boolean
  fallbackError: string
}

export interface PolledJob<T> {
  job: Ref<T | null>
  isLoading: Ref<boolean>
  loadError: Ref<string | null>
  notFound: Ref<boolean>
  // Show `job` in place of the current one — e.g. what a cancel request
  // returned — so a poll already in flight can't overwrite it with an older
  // snapshot; polling carries on only if `job` is still active.
  replace: (job: T) => void
}

export function usePolledJob<T>(options: PolledJobOptions<T>): PolledJob<T> {
  const job = ref<T | null>(null) as Ref<T | null>
  const isLoading = ref(true)
  const loadError = ref<string | null>(null)
  const notFound = ref(false)

  let pollTimer: ReturnType<typeof setTimeout> | undefined
  // Bumped whenever a newer request makes any earlier one's resolution
  // irrelevant: another id, replace(), or the view unmounting mid-fetch
  // (which otherwise has no timer yet to clear, and would schedule an
  // uncancellable next poll once it resolves).
  let requestGeneration = 0

  function stop() {
    requestGeneration++
    clearTimeout(pollTimer)
  }

  function scheduleIfActive(current: T, id: number) {
    if (options.isActive(current)) pollTimer = setTimeout(() => poll(id), POLL_INTERVAL_MS)
  }

  async function poll(id: number) {
    const generation = requestGeneration
    try {
      const result = await options.load(id)
      if (generation !== requestGeneration) return
      job.value = result
      loadError.value = null
      scheduleIfActive(result, id)
    } catch (err) {
      if (generation !== requestGeneration) return
      if (options.isNotFound(err)) {
        notFound.value = true
      } else {
        loadError.value = err instanceof Error ? err.message : options.fallbackError
        pollTimer = setTimeout(() => poll(id), POLL_INTERVAL_MS)
      }
    } finally {
      if (generation === requestGeneration) isLoading.value = false
    }
  }

  watch(
    options.id,
    (id) => {
      stop()
      job.value = null
      loadError.value = null
      notFound.value = Number.isNaN(id)
      isLoading.value = !notFound.value
      if (!notFound.value) poll(id)
    },
    { immediate: true },
  )

  onScopeDispose(stop)

  function replace(current: T) {
    stop()
    job.value = current
    scheduleIfActive(current, options.id())
  }

  return { job, isLoading, loadError, notFound, replace }
}
