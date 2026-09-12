import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import AdminView from '../AdminView.vue'

const listAccountsMock = vi.hoisted(() => vi.fn())
const createAccountMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/admin', () => ({
  listAccounts: listAccountsMock,
  createAccount: createAccountMock,
}))

vi.mock('@/stores/session', () => ({
  session: { accessToken: { value: 'a-token' } },
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/admin', name: 'admin', component: AdminView },
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
    ],
  })
}

describe('AdminView', () => {
  afterEach(() => {
    listAccountsMock.mockReset()
    createAccountMock.mockReset()
  })

  it('loads and renders the account list', async () => {
    listAccountsMock.mockResolvedValue([
      { id: 1, email: 'jan.peeters@vives.be', displayName: 'Jan', isActive: true },
      { id: 2, email: 'inactive@vives.be', displayName: 'Inactive', isActive: false },
    ])
    const router = createTestRouter()
    router.push('/admin')
    await router.isReady()

    const wrapper = mount(AdminView, { global: { plugins: [router] } })
    await flushPromises()

    expect(listAccountsMock).toHaveBeenCalledWith('a-token')
    expect(wrapper.text()).toContain('jan.peeters@vives.be')
    expect(wrapper.text()).toContain('Active')
    expect(wrapper.text()).toContain('Inactive')
  })

  it('submits a new account and adds it to the list on success', async () => {
    listAccountsMock.mockResolvedValue([])
    createAccountMock.mockResolvedValue({
      id: 3,
      email: 'new.student@student.vives.be',
      displayName: 'New',
      isActive: true,
    })
    const router = createTestRouter()
    router.push('/admin')
    await router.isReady()

    const wrapper = mount(AdminView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.find('input#new-account-email').setValue('new.student@student.vives.be')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(createAccountMock).toHaveBeenCalledWith('a-token', 'new.student@student.vives.be')
    expect(wrapper.text()).toContain('new.student@student.vives.be')
    expect(wrapper.text()).toContain('Invite sent')
  })

  it('shows the error message when creating an account fails', async () => {
    listAccountsMock.mockResolvedValue([])
    createAccountMock.mockRejectedValue(
      new Error('An active account with this email already exists.'),
    )
    const router = createTestRouter()
    router.push('/admin')
    await router.isReady()

    const wrapper = mount(AdminView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.find('input#new-account-email').setValue('existing@vives.be')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('An active account with this email already exists.')
  })
})
