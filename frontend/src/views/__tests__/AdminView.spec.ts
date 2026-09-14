import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import AdminView from '../AdminView.vue'

const listAccountsMock = vi.hoisted(() => vi.fn())
const createAccountMock = vi.hoisted(() => vi.fn())
const deactivateAccountMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/admin', () => ({
  listAccounts: listAccountsMock,
  createAccount: createAccountMock,
  deactivateAccount: deactivateAccountMock,
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
    deactivateAccountMock.mockReset()
  })

  it('loads and renders the account list', async () => {
    listAccountsMock.mockResolvedValue([
      { id: 1, email: 'jan.peeters@vives.be', displayName: 'Jan', role: 'user', isActive: true },
      {
        id: 2,
        email: 'inactive@vives.be',
        displayName: 'Inactive',
        role: 'user',
        isActive: false,
      },
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
      role: 'user',
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

  it('deactivates an active account and updates its row', async () => {
    listAccountsMock.mockResolvedValue([
      { id: 1, email: 'jan.peeters@vives.be', displayName: 'Jan', role: 'user', isActive: true },
      {
        id: 2,
        email: 'inactive@vives.be',
        displayName: 'Inactive',
        role: 'user',
        isActive: false,
      },
    ])
    deactivateAccountMock.mockResolvedValue({
      id: 1,
      email: 'jan.peeters@vives.be',
      displayName: 'Jan',
      role: 'user',
      isActive: false,
    })
    const router = createTestRouter()
    router.push('/admin')
    await router.isReady()

    const wrapper = mount(AdminView, { global: { plugins: [router] } })
    await flushPromises()

    // Only the active row gets a deactivate action.
    const buttons = wrapper.findAll('button[data-testid="deactivate"]')
    expect(buttons).toHaveLength(1)

    await buttons[0]!.trigger('click')
    await flushPromises()

    expect(deactivateAccountMock).toHaveBeenCalledWith('a-token', 1)
    expect(wrapper.findAll('button[data-testid="deactivate"]')).toHaveLength(0)
    const rows = wrapper.findAll('tbody tr')
    expect(rows[0]!.text()).toContain('Inactive')
  })

  it('never shows a deactivate action for an Admin row', async () => {
    listAccountsMock.mockResolvedValue([
      {
        id: 1,
        email: 'admin.person@vives.be',
        displayName: 'Admin',
        role: 'admin',
        isActive: true,
      },
      { id: 2, email: 'jan.peeters@vives.be', displayName: 'Jan', role: 'user', isActive: true },
    ])
    const router = createTestRouter()
    router.push('/admin')
    await router.isReady()

    const wrapper = mount(AdminView, { global: { plugins: [router] } })
    await flushPromises()

    // Only the User row gets a deactivate action, even though both are active.
    expect(wrapper.findAll('button[data-testid="deactivate"]')).toHaveLength(1)
  })

  it('shows the backend-provided reason when deactivating an account fails', async () => {
    listAccountsMock.mockResolvedValue([
      { id: 1, email: 'jan.peeters@vives.be', displayName: 'Jan', role: 'user', isActive: true },
    ])
    deactivateAccountMock.mockRejectedValue(new Error('Account not found.'))
    const router = createTestRouter()
    router.push('/admin')
    await router.isReady()

    const wrapper = mount(AdminView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.find('button[data-testid="deactivate"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Account not found.')
    expect(wrapper.findAll('button[data-testid="deactivate"]')).toHaveLength(1)
  })

  it('keeps an in-flight row deactivating even if another row is deactivated first', async () => {
    listAccountsMock.mockResolvedValue([
      { id: 1, email: 'a@vives.be', displayName: 'A', role: 'user', isActive: true },
      { id: 2, email: 'b@vives.be', displayName: 'B', role: 'user', isActive: true },
    ])
    let resolveFirst!: (value: unknown) => void
    deactivateAccountMock.mockImplementationOnce(
      () => new Promise((resolve) => (resolveFirst = resolve)),
    )
    deactivateAccountMock.mockResolvedValueOnce({
      id: 2,
      email: 'b@vives.be',
      displayName: 'B',
      role: 'user',
      isActive: false,
    })
    const router = createTestRouter()
    router.push('/admin')
    await router.isReady()

    const wrapper = mount(AdminView, { global: { plugins: [router] } })
    await flushPromises()

    const buttons = wrapper.findAll('button[data-testid="deactivate"]')
    await buttons[0]!.trigger('click') // A: left pending
    await buttons[1]!.trigger('click') // B: resolves immediately
    await flushPromises()

    // A's button must still show its in-flight state, unaffected by B finishing.
    const rowA = wrapper.findAll('tbody tr')[0]!
    expect(rowA.text()).toContain('Deactivating…')
    expect(rowA.find('button[data-testid="deactivate"]').attributes('disabled')).toBeDefined()

    resolveFirst({ id: 1, email: 'a@vives.be', displayName: 'A', role: 'user', isActive: false })
    await flushPromises()
  })
})
