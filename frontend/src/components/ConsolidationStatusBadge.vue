<script setup lang="ts">
import { computed } from 'vue'

import type { ConsolidationStatus } from '@/services/consolidations'

const props = defineProps<{ status: ConsolidationStatus }>()

// Keyed by every ConsolidationStatus (ticket #122), so adding a status to
// the type without deciding how it looks is a type error rather than a row
// that silently renders nothing. `processing` is shown exactly as the
// backend reports it — whether it's stale is the #121 reconciler's call,
// which fails such rows server-side, never this component's.
const STATUS_BADGES: Record<
  ConsolidationStatus,
  { label: string; toneClasses: string; spinner: boolean }
> = {
  processing: { label: 'Processing…', toneClasses: 'border-warning text-warning', spinner: true },
  completed: { label: 'Completed', toneClasses: 'border-success text-success', spinner: false },
  failed: { label: 'Failed', toneClasses: 'border-danger text-danger', spinner: false },
}

const badge = computed(() => STATUS_BADGES[props.status])
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium"
    :class="badge.toneClasses"
  >
    <span
      v-if="badge.spinner"
      aria-hidden="true"
      class="h-2.5 w-2.5 rounded-full border-2 border-current border-t-transparent motion-safe:animate-spin"
    />
    {{ badge.label }}
  </span>
</template>
