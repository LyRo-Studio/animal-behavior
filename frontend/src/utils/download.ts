// The synthetic <a>/click/remove dance every browser-triggered (non-
// navigation) download in this app needs — shared by MediaBrowserView's
// Cut download (a media-token-scoped stream URL) and AnalysisView's report
// download (a Blob object URL) — neither can be exposed as an ordinary
// clickable link, and re-fetching the same URL by navigating to it directly
// would either re-mint a token needlessly or, for a Blob URL, not work at
// all outside the page that created it.
export function triggerBrowserDownload(url: string, filename?: string): void {
  const link = document.createElement('a')
  link.href = url
  link.rel = 'noreferrer'
  if (filename) {
    link.download = filename
  }
  document.body.appendChild(link)
  link.click()
  link.remove()
}
