import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createAnalysis, createWholesaleAnalysis } from '../analyses'

const JOB_RESPONSE = {
  id: 7,
  test_ids: ['T001', 'T002'],
  requested_by_identity: null,
  status: 'queued',
  dogtrace_version: null,
  report_available: false,
  created_at: '2026-01-01T00:00:00Z',
  started_at: null,
  finished_at: null,
  videos: [],
}

// Ticket #89's unified POST /analyses body: `{test_ids, cuts}`, where `cuts`
// given is only valid for exactly one Test and omitted means wholesale.
describe('creating an analysis', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockResolvedValue(new Response(JSON.stringify(JOB_RESPONSE), { status: 201 }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    fetchMock.mockReset()
  })

  function sentBody(): unknown {
    const [, init] = fetchMock.mock.calls[0]!
    return JSON.parse(init.body)
  }

  it('sends a single Test with its hand-picked Cuts', async () => {
    await createAnalysis('T001', ['cuts/T001/T001_C2_ME_F1.mp4'])

    expect(sentBody()).toEqual({ test_ids: ['T001'], cuts: ['cuts/T001/T001_C2_ME_F1.mp4'] })
  })

  it('sends a wholesale request for several Tests with `cuts` omitted entirely', async () => {
    const job = await createWholesaleAnalysis(['T001', 'T002'])

    expect(sentBody()).toEqual({ test_ids: ['T001', 'T002'] })
    expect(job.testIds).toEqual(['T001', 'T002'])
  })

  it("surfaces the backend's limit message when a wholesale request is rejected", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Select at most 10 Tests per analysis.' }), {
        status: 400,
      }),
    )

    await expect(createWholesaleAnalysis(['T001', 'T002'])).rejects.toThrow(
      'Select at most 10 Tests per analysis.',
    )
  })
})
