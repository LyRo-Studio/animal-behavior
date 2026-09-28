import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { API_BASE_URL } from '../apiBase'
import { createAnalysis, createWholesaleAnalysis, listAnalysisTraces } from '../analyses'

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

// Issue #133: each video's trace images, with URLs the view can use directly
// as an <img src> and a download link.
describe('listing trace images', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    fetchMock.mockReset()
  })

  it('gives every image a view URL and a download URL', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            cut_key: 'cuts/T001/T001_C2_ME_F1.mp4',
            images: [
              {
                path: 'traces/T001_C2_ME_F1_dog_trace.jpg',
                filename: 'T001_C2_ME_F1_dog_trace.jpg',
                label: 'dog_trace',
              },
            ],
          },
          { cut_key: 'cuts/T001/T001_C2_ME_F2.mp4', images: [] },
        ]),
        { status: 200 },
      ),
    )

    const traces = await listAnalysisTraces(42)

    expect(fetchMock.mock.calls[0]![0]).toBe(`${API_BASE_URL}/analyses/42/traces`)
    const base = `${API_BASE_URL}/analyses/42/traces/traces/T001_C2_ME_F1_dog_trace.jpg`
    expect(traces).toEqual([
      {
        cutKey: 'cuts/T001/T001_C2_ME_F1.mp4',
        images: [
          {
            filename: 'T001_C2_ME_F1_dog_trace.jpg',
            label: 'dog_trace',
            url: base,
            downloadUrl: `${base}?download=true`,
          },
        ],
      },
      { cutKey: 'cuts/T001/T001_C2_ME_F2.mp4', images: [] },
    ])
  })

  it('encodes each path segment but keeps the slashes between them', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            cut_key: 'cuts/T001/T001_C2_ME_F1.mp4',
            images: [
              { path: 'a b/T001_C2_ME_F1_#1.jpg', filename: 'T001_C2_ME_F1_#1.jpg', label: '#1' },
            ],
          },
        ]),
        { status: 200 },
      ),
    )

    const [video] = await listAnalysisTraces(42)

    expect(video!.images[0]!.url).toBe(
      `${API_BASE_URL}/analyses/42/traces/a%20b/T001_C2_ME_F1_%231.jpg`,
    )
  })

  it("surfaces the backend's message when the list can't be loaded", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ detail: 'No trace images are available for this analysis yet.' }),
        { status: 409 },
      ),
    )

    await expect(listAnalysisTraces(42)).rejects.toThrow(
      'No trace images are available for this analysis yet.',
    )
  })
})
