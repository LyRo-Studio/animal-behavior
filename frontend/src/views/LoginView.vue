<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { session } from '@/stores/session'

const GENERIC_ERROR = 'Invalid email or password'

const route = useRoute()
const router = useRouter()

const email = ref('')
const password = ref('')
const error = ref<string | null>(null)
const isSubmitting = ref(false)

async function handleSubmit() {
  error.value = null
  isSubmitting.value = true

  try {
    await session.login(email.value, password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.push(redirect)
  } catch {
    // Never distinguish *why* login failed — mirrors the backend's own
    // single generic failure response.
    error.value = GENERIC_ERROR
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <main
    class="flex min-h-screen items-center justify-center bg-background font-sans text-foreground"
  >
    <form
      class="w-full max-w-sm rounded-lg border border-border bg-surface p-8 shadow-sm"
      @submit.prevent="handleSubmit"
    >
      <h1 class="font-serif text-2xl text-primary">Hogeschool VIVES</h1>
      <p class="mt-1 text-sm text-muted">Log in to continue.</p>

      <label class="mt-6 block text-sm font-medium text-foreground" for="email">Email</label>
      <input
        id="email"
        v-model="email"
        type="email"
        autocomplete="username"
        required
        class="mt-1 w-full rounded-md border border-border bg-surface px-3 py-2 text-foreground focus:border-primary focus:outline-none"
      />

      <label class="mt-4 block text-sm font-medium text-foreground" for="password">Password</label>
      <input
        id="password"
        v-model="password"
        type="password"
        autocomplete="current-password"
        required
        class="mt-1 w-full rounded-md border border-border bg-surface px-3 py-2 text-foreground focus:border-primary focus:outline-none"
      />

      <p v-if="error" class="mt-4 text-sm text-danger" role="alert">{{ error }}</p>

      <button
        type="submit"
        :disabled="isSubmitting"
        class="mt-6 w-full rounded-md bg-primary px-4 py-2 font-medium text-white hover:bg-primary-hover disabled:opacity-60"
      >
        {{ isSubmitting ? 'Logging in…' : 'Log in' }}
      </button>

      <RouterLink
        :to="{ name: 'forgot-password' }"
        class="mt-4 block text-center text-sm font-medium text-primary hover:underline"
      >
        Forgot password?
      </RouterLink>
    </form>
  </main>
</template>
