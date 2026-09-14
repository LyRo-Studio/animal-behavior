<script setup lang="ts">
import { ref } from 'vue'

import { requestPasswordReset } from '@/services/auth'

// One fixed message regardless of outcome — mirrors the backend's generic
// response, so this page is never an oracle for whether an email has an
// account (ENGINEERING-STANDARDS.md's password-security guidance).
const CONFIRMATION_MESSAGE =
  "If an account exists for that email, we've sent a link to reset your password."

const email = ref('')
const isSubmitting = ref(false)
const isSubmitted = ref(false)

async function handleSubmit() {
  isSubmitting.value = true
  try {
    await requestPasswordReset(email.value)
  } catch {
    // Deliberately swallowed: the confirmation below is identical whether
    // the request succeeded, the email had no matching account, or the
    // network call itself failed — never an oracle for which.
  } finally {
    isSubmitting.value = false
    isSubmitted.value = true
  }
}
</script>

<template>
  <main
    class="flex min-h-screen items-center justify-center bg-background font-sans text-foreground"
  >
    <div class="w-full max-w-sm rounded-lg border border-border bg-surface p-8 shadow-sm">
      <h1 class="font-serif text-2xl text-primary">Reset your password</h1>

      <template v-if="isSubmitted">
        <p class="mt-4 text-sm text-foreground" role="status">{{ CONFIRMATION_MESSAGE }}</p>
      </template>

      <form v-else class="contents" @submit.prevent="handleSubmit">
        <p class="mt-1 text-sm text-muted">
          Enter your email and we'll send you a link to set a new password.
        </p>

        <label class="mt-6 block text-sm font-medium text-foreground" for="email">Email</label>
        <input
          id="email"
          v-model="email"
          type="email"
          autocomplete="username"
          required
          class="mt-1 w-full rounded-md border border-border bg-surface px-3 py-2 text-foreground focus:border-primary focus:outline-none"
        />

        <button
          type="submit"
          :disabled="isSubmitting"
          class="mt-6 w-full rounded-md bg-primary px-4 py-2 font-medium text-white hover:bg-primary-hover disabled:opacity-60"
        >
          {{ isSubmitting ? 'Sending…' : 'Send reset link' }}
        </button>
      </form>

      <RouterLink
        :to="{ name: 'login' }"
        class="mt-6 block text-sm font-medium text-primary hover:underline"
      >
        Back to login
      </RouterLink>
    </div>
  </main>
</template>
