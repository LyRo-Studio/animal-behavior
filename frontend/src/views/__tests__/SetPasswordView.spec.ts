import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import SetPasswordView from '../SetPasswordView.vue'

const setPasswordMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/accountActions', () => ({
  setPassword: setPasswordMock,
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/set-password', name: 'set-password', component: SetPasswordView },
      { path: '/login', name: 'login', component: { template: '<div>login</div>' } },
    ],
  })
}

describe('SetPasswordView', () => {
  afterEach(() => {
    setPasswordMock.mockReset()
  })

  it('reads the token from the URL and submits it with the new password', async () => {
    setPasswordMock.mockResolvedValue(undefined)
    const router = createTestRouter()
    router.push({ name: 'set-password', query: { token: 'abc123' } })
    await router.isReady()

    const wrapper = mount(SetPasswordView, { global: { plugins: [router] } })
    await wrapper.find('#password').setValue('a-new-strong-password')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(setPasswordMock).toHaveBeenCalledWith('abc123', 'a-new-strong-password')
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('shows a clear error for an invalid or expired token', async () => {
    setPasswordMock.mockRejectedValue(new Error('This link is invalid or has expired.'))
    const router = createTestRouter()
    router.push({ name: 'set-password', query: { token: 'expired-token' } })
    await router.isReady()

    const wrapper = mount(SetPasswordView, { global: { plugins: [router] } })
    await wrapper.find('#password').setValue('a-new-strong-password')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('This link is invalid or has expired.')
    expect(router.currentRoute.value.name).toBe('set-password')
  })
})
