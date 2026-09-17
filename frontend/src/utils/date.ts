// Shared by every view that shows a backend ISO timestamp to a user
// (MediaBrowserView's Cut "Last modified" and previous-analyses panel,
// AnalysesHistoryView's job list) — one place to change if the display
// format ever needs to (e.g. a fixed locale or relative-time format).
export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString()
}
