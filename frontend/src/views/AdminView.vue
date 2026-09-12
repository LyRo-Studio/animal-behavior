<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { createAccount, listAccounts, type AdminAccount } from '@/services/admin'
import { session } from '@/stores/session'

const accounts = ref<AdminAccount[]>([])
const isLoading = ref(true)
const loadError = ref<string | null>(null)

const email = ref('')
const submitError = ref<string | null>(null)
const successMessage = ref<string | null>(null)
const isSubmitting = ref(false)

async function loadAccounts() {
  if (!session.accessToken.value) return

  isLoading.value = true
  loadError.value = null
  try {
    accounts.value = await listAccounts(session.accessToken.value)
  } catch {
    loadError.value = 'Failed to load accounts.'
  } finally {
    isLoading.value = false
  }
}

async function handleSubmit() {
  if (!session.accessToken.value) return

  submitError.value = null
  successMessage.value = null
  isSubmitting.value = true

  try {
    const account = await createAccount(session.accessToken.value, email.value)
    accounts.value = [...accounts.value, account].sort((a, b) => a.email.localeCompare(b.email))
    successMessage.value = `Invite sent to ${account.email}.`
    email.value = ''
  } catch (err) {
    submitError.value = err instanceof Error ? err.message : 'Failed to create account.'
  } finally {
    isSubmitting.value = false
  }
}

onMounted(loadAccounts)
</script>

<template>
  <main class="min-h-screen bg-background font-sans text-foreground">
    <header class="flex items-center justify-between border-b border-border bg-surface px-6 py-4">
      <h1 class="font-serif text-xl text-primary">Admin</h1>
      <RouterLink :to="{ name: 'home' }" class="text-sm font-medium text-primary hover:underline">
        Back to Home
      </RouterLink>
    </header>

    <section class="mx-auto max-w-2xl px-6 py-10">
      <h2 class="text-lg font-medium text-foreground">Add a User</h2>
      <form class="mt-4 flex items-end gap-3" @submit.prevent="handleSubmit">
        <div class="flex-1">
          <label class="block text-sm font-medium text-foreground" for="new-account-email">
            Email
          </label>
          <input
            id="new-account-email"
            v-model="email"
            type="email"
            required
            placeholder="jan.peeters@vives.be"
            class="mt-1 w-full rounded-md border border-border bg-surface px-3 py-2 text-foreground focus:border-primary focus:outline-none"
          />
        </div>
        <button
          type="submit"
          :disabled="isSubmitting"
          class="rounded-md bg-primary px-4 py-2 font-medium text-white hover:bg-primary-hover disabled:opacity-60"
        >
          {{ isSubmitting ? 'Adding…' : 'Add User' }}
        </button>
      </form>

      <p v-if="submitError" class="mt-3 text-sm text-danger" role="alert">{{ submitError }}</p>
      <p v-if="successMessage" class="mt-3 text-sm text-success">{{ successMessage }}</p>

      <h2 class="mt-10 text-lg font-medium text-foreground">Accounts</h2>
      <p v-if="isLoading" class="mt-2 text-sm text-muted">Loading…</p>
      <p v-else-if="loadError" class="mt-2 text-sm text-danger" role="alert">{{ loadError }}</p>
      <table v-else class="mt-4 w-full text-left text-sm">
        <thead>
          <tr class="border-b border-border text-muted">
            <th class="py-2 pr-4 font-medium">Email</th>
            <th class="py-2 pr-4 font-medium">Name</th>
            <th class="py-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="account in accounts" :key="account.id" class="border-b border-border">
            <td class="py-2 pr-4">{{ account.email }}</td>
            <td class="py-2 pr-4">{{ account.displayName }}</td>
            <td class="py-2">
              <span :class="account.isActive ? 'text-success' : 'text-muted'">
                {{ account.isActive ? 'Active' : 'Inactive' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </main>
</template>
