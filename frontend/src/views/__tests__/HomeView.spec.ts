import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import { session } from '@/stores/session'

import HomeView from '../HomeView.vue'

const logoutMock = vi.hoisted(() => vi.fn())

vi.mock('@/stores/session', () => ({
  session: { logout: logoutMock, currentAccount: { value: null } },
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/', name: 'home', component: HomeView },
      { path: '/login', name: 'login', component: { template: '<div>login</div>' } },
      { path: '/admin', name: 'admin', component: { template: '<div>admin</div>' } },
    ],
  })
}

describe('HomeView', () => {
  beforeEach(() => {
    session.currentAccount.value = {
      id: 1,
      email: 'jan.peeters@vives.be',
      displayName: 'Jan',
      role: 'user',
    }
  })

  afterEach(() => {
    logoutMock.mockReset()
  })

  it("renders the current account's display name", async () => {
    const router = createTestRouter()
    router.push('/')
    await router.isReady()

    const wrapper = mount(HomeView, { global: { plugins: [router] } })

    expect(wrapper.text()).toContain('Jan')
  })

  it('logs out and navigates to Login when the logout button is clicked', async () => {
    logoutMock.mockResolvedValue(undefined)
    const router = createTestRouter()
    router.push('/')
    await router.isReady()

    const wrapper = mount(HomeView, { global: { plugins: [router] } })
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(logoutMock).toHaveBeenCalledOnce()
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('does not show an Admin link for a User account', async () => {
    const router = createTestRouter()
    router.push('/')
    await router.isReady()

    const wrapper = mount(HomeView, { global: { plugins: [router] } })

    expect(wrapper.text()).not.toContain('Admin')
  })

  it('shows an Admin link for an Admin account', async () => {
    session.currentAccount.value = {
      id: 2,
      email: 'admin.person@vives.be',
      displayName: 'Admin',
      role: 'admin',
    }
    const router = createTestRouter()
    router.push('/')
    await router.isReady()

    const wrapper = mount(HomeView, { global: { plugins: [router] } })

    const adminLink = wrapper.findComponent({ name: 'RouterLink' })
    expect(adminLink.exists()).toBe(true)
    expect(wrapper.text()).toContain('Admin')
  })
})
