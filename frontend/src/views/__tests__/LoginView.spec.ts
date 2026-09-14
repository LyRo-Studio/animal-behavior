import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import LoginView from '../LoginView.vue'

const loginMock = vi.hoisted(() => vi.fn())

vi.mock('@/stores/session', () => ({
  session: { login: loginMock },
}))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/login', name: 'login', component: LoginView },
      { path: '/', name: 'home', component: { template: '<div>home</div>' } },
      {
        path: '/forgot-password',
        name: 'forgot-password',
        component: { template: '<div>forgot-password</div>' },
      },
    ],
  })
}

describe('LoginView', () => {
  afterEach(() => {
    loginMock.mockReset()
  })

  it('logs in with the entered credentials and navigates to home on success', async () => {
    loginMock.mockResolvedValue(undefined)
    const router = createTestRouter()
    router.push('/login')
    await router.isReady()

    const wrapper = mount(LoginView, { global: { plugins: [router] } })
    await wrapper.find('#email').setValue('jan.peeters@vives.be')
    await wrapper.find('#password').setValue('correct-horse-battery-staple')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(loginMock).toHaveBeenCalledWith('jan.peeters@vives.be', 'correct-horse-battery-staple')
    expect(router.currentRoute.value.name).toBe('home')
  })

  it('shows a generic error message on failed login, without navigating', async () => {
    loginMock.mockRejectedValue(new Error('Invalid email or password'))
    const router = createTestRouter()
    router.push('/login')
    await router.isReady()

    const wrapper = mount(LoginView, { global: { plugins: [router] } })
    await wrapper.find('#email').setValue('jan.peeters@vives.be')
    await wrapper.find('#password').setValue('wrong-password')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Invalid email or password')
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('links to the forgot-password page', async () => {
    const router = createTestRouter()
    router.push('/login')
    await router.isReady()

    const wrapper = mount(LoginView, { global: { plugins: [router] } })

    const forgotPasswordLink = wrapper.findComponent({ name: 'RouterLink' })
    expect(forgotPasswordLink.props('to')).toEqual({ name: 'forgot-password' })
  })
})
