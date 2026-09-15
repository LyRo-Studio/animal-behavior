<script setup lang="ts">
import { useRouter } from 'vue-router'

import { session } from '@/stores/session'

const router = useRouter()

async function handleLogout() {
  await session.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <main class="min-h-screen bg-background font-sans text-foreground">
    <header class="flex items-center justify-between border-b border-border bg-surface px-6 py-4">
      <h1 class="font-serif text-xl text-primary">Hogeschool VIVES</h1>
      <div class="flex items-center gap-4">
        <!-- Every authenticated Account gets this link (CONTEXT.md's
             "Media browser — access" decision) — no role gating, unlike
             Admin below. -->
        <RouterLink
          :to="{ name: 'media' }"
          class="text-sm font-medium text-primary hover:underline"
        >
          Media Browser
        </RouterLink>
        <!-- Only Admins see this link (CONTEXT.md's "Admin page" entry) —
             a User attempting the route directly is redirected away by the
             router guard regardless, this just avoids showing an
             affordance a User can't use. -->
        <RouterLink
          v-if="session.currentAccount.value?.role === 'admin'"
          :to="{ name: 'admin' }"
          class="text-sm font-medium text-primary hover:underline"
        >
          Admin
        </RouterLink>
        <span v-if="session.currentAccount.value" class="text-sm text-muted">
          {{ session.currentAccount.value.displayName }}
        </span>
        <button
          type="button"
          class="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground hover:bg-background"
          @click="handleLogout"
        >
          Log out
        </button>
      </div>
    </header>

    <!-- Static for this round (CONTEXT.md's "Home content" decision) — not
         an editable/CMS-backed section yet. -->
    <section class="mx-auto max-w-3xl px-6 py-10">
      <h2 class="text-lg font-medium text-foreground">Welcome</h2>
      <p class="mt-2 text-muted">
        This is the Animal Behavior application for Hogeschool VIVES. The animal-behavior features
        themselves are still being built — for now, this page confirms you're logged in.
      </p>
    </section>
  </main>
</template>
