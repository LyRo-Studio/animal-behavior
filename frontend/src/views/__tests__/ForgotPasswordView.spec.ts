import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import ForgotPasswordView from '../ForgotPasswordView.vue'

const requestPasswordResetMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/auth', () => ({
  requestPasswordReset: requestPasswordResetMock,
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/forgot-password', name: 'forgot-password', component: ForgotPasswordView },
      { path: '/login', name: 'login', component: { template: '<div>login</div>' } },
    ],
  })
}

describe('ForgotPasswordView', () => {
  afterEach(() => {
    requestPasswordResetMock.mockReset()
  })

  it('submits the entered email and shows the same generic confirmation on success', async () => {
    requestPasswordResetMock.mockResolvedValue(undefined)
    const router = createTestRouter()
    router.push('/forgot-password')
    await router.isReady()

    const wrapper = mount(ForgotPasswordView, { global: { plugins: [router] } })
    await wrapper.find('#email').setValue('jan.peeters@vives.be')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(requestPasswordResetMock).toHaveBeenCalledWith('jan.peeters@vives.be')
    expect(wrapper.text()).toContain(
      "If an account exists for that email, we've sent a link to reset your password.",
    )
  })

  it('shows the identical confirmation even when the request fails, never revealing the outcome', async () => {
    requestPasswordResetMock.mockRejectedValue(new Error('network error'))
    const router = createTestRouter()
    router.push('/forgot-password')
    await router.isReady()

    const wrapper = mount(ForgotPasswordView, { global: { plugins: [router] } })
    await wrapper.find('#email').setValue('unknown@vives.be')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain(
      "If an account exists for that email, we've sent a link to reset your password.",
    )
  })

  it('links back to the login page', async () => {
    const router = createTestRouter()
    router.push('/forgot-password')
    await router.isReady()

    const wrapper = mount(ForgotPasswordView, { global: { plugins: [router] } })

    expect(wrapper.findComponent({ name: 'RouterLink' }).props('to')).toEqual({ name: 'login' })
  })
})
