<script setup lang="ts">
import { onMounted, ref, useId } from 'vue'

// Ticket #101: a modal "are you sure?" for an action that overwrites
// something, first used for re-cutting a Test that already has Cuts (#96).
// Rendered only while it's needed (the parent's `v-if`); focus starts on
// Cancel so an accidental Enter never confirms, and Tab stays inside.

withDefaults(
  defineProps<{
    title: string
    confirmLabel: string
    cancelLabel?: string
  }>(),
  { cancelLabel: 'Cancel' },
)

const emit = defineEmits<{ confirm: []; cancel: [] }>()

const titleId = useId()
const cancelButton = ref<HTMLButtonElement | null>(null)
const confirmButton = ref<HTMLButtonElement | null>(null)

onMounted(() => cancelButton.value?.focus())

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('cancel')
  } else if (event.key === 'Tab') {
    event.preventDefault()
    const next =
      document.activeElement === cancelButton.value ? confirmButton.value : cancelButton.value
    next?.focus()
  }
}
</script>

<template>
  <div class="fixed inset-0 z-10 flex items-center justify-center bg-foreground/40 px-6">
    <div
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      class="w-full max-w-md rounded-md border border-border bg-surface p-6 shadow-lg"
      @keydown="onKeydown"
    >
      <h2 :id="titleId" class="text-lg font-medium text-foreground">{{ title }}</h2>
      <div class="mt-3 text-sm text-foreground">
        <slot />
      </div>
      <div class="mt-6 flex justify-end gap-3">
        <button
          ref="cancelButton"
          type="button"
          data-testid="confirm-dialog-cancel"
          class="rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-background"
          @click="emit('cancel')"
        >
          {{ cancelLabel }}
        </button>
        <button
          ref="confirmButton"
          type="button"
          data-testid="confirm-dialog-confirm"
          class="rounded-md bg-danger px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          @click="emit('confirm')"
        >
          {{ confirmLabel }}
        </button>
      </div>
    </div>
  </div>
</template>
