<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

import { fetchHealth } from '@/services/health'

// The backend container can still be starting when this page first loads
// (docker-compose's healthcheck closes most of that race, but not all of
// it) — retry instead of getting stuck on one bad first attempt.
const RETRY_DELAY_MS = 3000

const status = ref<'checking' | 'ok' | 'unreachable'>('checking')
let retryTimer: ReturnType<typeof setTimeout> | undefined

async function checkHealth() {
  try {
    const health = await fetchHealth()
    status.value = health.status === 'ok' ? 'ok' : 'unreachable'
  } catch {
    status.value = 'unreachable'
  }

  if (status.value !== 'ok') {
    retryTimer = setTimeout(checkHealth, RETRY_DELAY_MS)
  }
}

onMounted(checkHealth)

onUnmounted(() => {
  clearTimeout(retryTimer)
})
</script>

<template>
  <main
    class="flex min-h-screen items-center justify-center bg-background font-sans text-foreground"
  >
    <div class="rounded-lg border border-border bg-surface p-8 text-center shadow-sm">
      <h1 class="font-serif text-2xl text-primary">Hogeschool VIVES</h1>
      <p class="mt-4 text-muted">
        Backend status:
        <span
          class="font-medium"
          :class="{
            'text-muted': status === 'checking',
            'text-success': status === 'ok',
            'text-danger': status === 'unreachable',
          }"
          >{{ status }}</span
        >
      </p>
    </div>
  </main>
</template>
