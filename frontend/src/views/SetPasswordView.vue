<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { setPassword } from '@/services/accountActions'

const route = useRoute()
const router = useRouter()

const token = typeof route.query.token === 'string' ? route.query.token : ''
const password = ref('')
const error = ref<string | null>(null)
const isSubmitting = ref(false)

async function handleSubmit() {
  error.value = null
  isSubmitting.value = true

  try {
    await setPassword(token, password.value)
    await router.push({ name: 'login' })
  } catch {
    error.value = 'This link is invalid or has expired.'
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
      <h1 class="font-serif text-2xl text-primary">Set your password</h1>
      <p class="mt-1 text-sm text-muted">Choose a password to finish setting up your account.</p>

      <label class="mt-6 block text-sm font-medium text-foreground" for="password">
        New password
      </label>
      <input
        id="password"
        v-model="password"
        type="password"
        autocomplete="new-password"
        minlength="8"
        required
        class="mt-1 w-full rounded-md border border-border bg-surface px-3 py-2 text-foreground focus:border-primary focus:outline-none"
      />

      <p v-if="error" class="mt-4 text-sm text-danger" role="alert">{{ error }}</p>

      <button
        type="submit"
        :disabled="isSubmitting"
        class="mt-6 w-full rounded-md bg-primary px-4 py-2 font-medium text-white hover:bg-primary-hover disabled:opacity-60"
      >
        {{ isSubmitting ? 'Saving…' : 'Set password' }}
      </button>
    </form>
  </main>
</template>
