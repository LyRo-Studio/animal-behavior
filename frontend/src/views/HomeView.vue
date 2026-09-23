<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchIdentity } from '@/services/whoami'

// Ticket #72: no login of our own — Mechatronics authenticates the person
// and the backend echoes back the identity it forwarded. Purely
// informational, so a failed lookup just leaves it out rather than
// blocking the page.
const identity = ref<string | null>(null)

onMounted(async () => {
  try {
    identity.value = await fetchIdentity()
  } catch {
    identity.value = null
  }
})
</script>

<template>
  <main class="min-h-screen bg-background font-sans text-foreground">
    <header class="flex items-center justify-between border-b border-border bg-surface px-6 py-4">
      <h1 class="font-serif text-xl text-primary">Hogeschool VIVES</h1>
      <div class="flex items-center gap-4">
        <RouterLink
          :to="{ name: 'media' }"
          class="text-sm font-medium text-primary hover:underline"
        >
          Media Browser
        </RouterLink>
        <RouterLink
          :to="{ name: 'analyses-history' }"
          class="text-sm font-medium text-primary hover:underline"
        >
          Analyses
        </RouterLink>
        <RouterLink
          :to="{ name: 'consolidation' }"
          class="text-sm font-medium text-primary hover:underline"
        >
          Data Consolidation
        </RouterLink>
        <span v-if="identity" class="text-sm text-muted" data-testid="identity">
          Signed in as {{ identity }}
        </span>
      </div>
    </header>

    <!-- Static for this round (CONTEXT.md's "Home content" decision) — not
         an editable/CMS-backed section yet. -->
    <section class="mx-auto max-w-3xl px-6 py-10">
      <h2 class="text-lg font-medium text-foreground">Welcome</h2>
      <p class="mt-2 text-muted">
        This is the Animal Behavior application for Hogeschool VIVES. Browse Tests and their Cuts in
        the Media Browser, and run and review analyses from there or from Analyses.
      </p>
    </section>
  </main>
</template>
