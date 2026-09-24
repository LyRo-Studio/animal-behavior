import type { AnalysisJob } from '@/services/analyses'

export interface TestBreakdown {
  testId: string
  succeeded: number
  failed: number
  total: number
}

// Issue #92 (Feature B): how each of a job's Tests fared, grouped from its
// per-video rows by the Test embedded in each Cut key (`cuts/<Test>/<file>`)
// — a display aggregation only, never a stored or independent per-Test
// status (CONTEXT.md's "No new job status" decision). One entry per Test in
// the job's own submission order, including a Test that contributed no
// videos at all.
export function breakdownByTest(job: Pick<AnalysisJob, 'testIds' | 'videos'>): TestBreakdown[] {
  const byTest = new Map<string, TestBreakdown>(
    job.testIds.map((testId) => [testId, { testId, succeeded: 0, failed: 0, total: 0 }]),
  )
  for (const video of job.videos) {
    const entry = byTest.get(video.cutKey.split('/')[1] ?? '')
    if (!entry) continue
    entry.total += 1
    if (video.status === 'succeeded') entry.succeeded += 1
    if (video.status === 'failed') entry.failed += 1
  }
  return [...byTest.values()]
}

// Whether a job gets a per-Test breakdown at all: only when it spans more
// than one Test (for a single Test it would just repeat the job-level
// counts), and never once cancelled (none of its videos ran). Shared by
// AnalysisView and AnalysesHistoryView so the two can't drift apart.
export function hasTestBreakdown(job: Pick<AnalysisJob, 'testIds' | 'status'>): boolean {
  return job.testIds.length > 1 && job.status !== 'cancelled'
}
