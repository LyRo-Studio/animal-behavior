<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  createAccount,
  deactivateAccount,
  DeactivatedAccountExistsError,
  listAccounts,
  reactivateAccount,
  type AdminAccount,
} from '@/services/admin'
import { session } from '@/stores/session'

const accounts = ref<AdminAccount[]>([])
const isLoading = ref(true)
const loadError = ref<string | null>(null)

const email = ref('')
const submitError = ref<string | null>(null)
const successMessage = ref<string | null>(null)
const isSubmitting = ref(false)
// Set when createAccount fails because the email belongs to a deactivated
// account — offers reactivating that account right from the form error,
// instead of leaving the Admin to hunt for it in the table below.
const reactivateOfferId = ref<number | null>(null)

const rowActionError = ref<string | null>(null)
// Sets, not single ids: two rows can be mid-request at once, and each
// row's button must reflect only its own request's state.
const deactivatingIds = ref<Set<number>>(new Set())
const reactivatingIds = ref<Set<number>>(new Set())

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
  reactivateOfferId.value = null
  isSubmitting.value = true

  try {
    const account = await createAccount(session.accessToken.value, email.value)
    accounts.value = [...accounts.value, account].sort((a, b) => a.email.localeCompare(b.email))
    successMessage.value = `Invite sent to ${account.email}.`
    email.value = ''
  } catch (err) {
    if (err instanceof DeactivatedAccountExistsError) {
      submitError.value = err.message
      reactivateOfferId.value = err.existingAccountId
    } else {
      submitError.value = err instanceof Error ? err.message : 'Failed to create account.'
    }
  } finally {
    isSubmitting.value = false
  }
}

async function handleDeactivate(account: AdminAccount) {
  if (!session.accessToken.value) return

  rowActionError.value = null
  deactivatingIds.value = new Set(deactivatingIds.value).add(account.id)

  try {
    const updated = await deactivateAccount(session.accessToken.value, account.id)
    accounts.value = accounts.value.map((existing) =>
      existing.id === updated.id ? updated : existing,
    )
  } catch (err) {
    rowActionError.value = err instanceof Error ? err.message : 'Failed to deactivate account.'
  } finally {
    const remaining = new Set(deactivatingIds.value)
    remaining.delete(account.id)
    deactivatingIds.value = remaining
  }
}

async function handleReactivate(id: number) {
  if (!session.accessToken.value) return

  // Reactivating from the create-form's "already exists" offer reports
  // its outcome inline on the form, not in the table's row-action banner.
  const isOffer = reactivateOfferId.value === id
  if (isOffer) {
    submitError.value = null
  } else {
    rowActionError.value = null
  }
  reactivatingIds.value = new Set(reactivatingIds.value).add(id)

  try {
    const updated = await reactivateAccount(session.accessToken.value, id)
    accounts.value = accounts.value.map((existing) =>
      existing.id === updated.id ? updated : existing,
    )
    if (isOffer) {
      reactivateOfferId.value = null
      successMessage.value = `Reactivated ${updated.email}.`
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to reactivate account.'
    if (isOffer) {
      submitError.value = message
    } else {
      rowActionError.value = message
    }
  } finally {
    const remaining = new Set(reactivatingIds.value)
    remaining.delete(id)
    reactivatingIds.value = remaining
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

      <div v-if="submitError" class="mt-3 text-sm text-danger" role="alert">
        <p>{{ submitError }}</p>
        <button
          v-if="reactivateOfferId !== null"
          type="button"
          data-testid="reactivate-offer"
          :disabled="reactivatingIds.has(reactivateOfferId)"
          class="mt-1 font-medium text-primary hover:underline disabled:opacity-60"
          @click="handleReactivate(reactivateOfferId)"
        >
          {{
            reactivatingIds.has(reactivateOfferId)
              ? 'Reactivating…'
              : 'Reactivate this account instead'
          }}
        </button>
      </div>
      <p v-if="successMessage" class="mt-3 text-sm text-success">{{ successMessage }}</p>

      <h2 class="mt-10 text-lg font-medium text-foreground">Accounts</h2>
      <p v-if="isLoading" class="mt-2 text-sm text-muted">Loading…</p>
      <p v-else-if="loadError" class="mt-2 text-sm text-danger" role="alert">{{ loadError }}</p>
      <template v-else>
        <p v-if="rowActionError" class="mt-2 text-sm text-danger" role="alert">
          {{ rowActionError }}
        </p>
        <table class="mt-4 w-full text-left text-sm">
          <thead>
            <tr class="border-b border-border text-muted">
              <th class="py-2 pr-4 font-medium">Email</th>
              <th class="py-2 pr-4 font-medium">Name</th>
              <th class="py-2 pr-4 font-medium">Status</th>
              <th class="py-2 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="account in accounts" :key="account.id" class="border-b border-border">
              <td class="py-2 pr-4">{{ account.email }}</td>
              <td class="py-2 pr-4">{{ account.displayName }}</td>
              <td class="py-2 pr-4">
                <span :class="account.isActive ? 'text-success' : 'text-muted'">
                  {{ account.isActive ? 'Active' : 'Inactive' }}
                </span>
              </td>
              <td class="py-2">
                <button
                  v-if="account.isActive && account.role !== 'admin'"
                  type="button"
                  data-testid="deactivate"
                  :disabled="deactivatingIds.has(account.id)"
                  class="text-sm font-medium text-danger hover:underline disabled:opacity-60"
                  @click="handleDeactivate(account)"
                >
                  {{ deactivatingIds.has(account.id) ? 'Deactivating…' : 'Deactivate' }}
                </button>
                <button
                  v-if="!account.isActive"
                  type="button"
                  data-testid="reactivate"
                  :disabled="reactivatingIds.has(account.id)"
                  class="text-sm font-medium text-primary hover:underline disabled:opacity-60"
                  @click="handleReactivate(account.id)"
                >
                  {{ reactivatingIds.has(account.id) ? 'Reactivating…' : 'Reactivate' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </template>
    </section>
  </main>
</template>
