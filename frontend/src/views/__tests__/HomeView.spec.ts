import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '../HomeView.vue'

const logoutMock = vi.hoisted(() => vi.fn())

vi.mock('@/stores/session', () => ({
  session: {
    logout: logoutMock,
    currentAccount: {
      value: { id: 1, email: 'jan.peeters@vives.be', displayName: 'Jan', role: 'user' },
    },
  },
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/', name: 'home', component: HomeView },
      { path: '/login', name: 'login', component: { template: '<div>login</div>' } },
    ],
  })
}

describe('HomeView', () => {
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
})
