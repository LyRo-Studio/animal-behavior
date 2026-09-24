import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import HomeView from '../HomeView.vue'

const fetchIdentityMock = vi.hoisted(() => vi.fn())

vi.mock('@/services/whoami', () => ({ fetchIdentity: fetchIdentityMock }))

function createTestRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/', name: 'home', component: HomeView },
      { path: '/media', name: 'media', component: { template: '<div>media</div>' } },
      {
        path: '/analyses',
        name: 'analyses-history',
        component: { template: '<div>analyses</div>' },
      },
      {
        path: '/consolidation',
        name: 'consolidation',
        component: { template: '<div>consolidation</div>' },
      },
      { path: '/cutting', name: 'cutting-upload', component: { template: '<div>cutting</div>' } },
    ],
  })
}

async function mountHome() {
  const router = createTestRouter()
  router.push('/')
  await router.isReady()
  const wrapper = mount(HomeView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

describe('HomeView', () => {
  beforeEach(() => {
    fetchIdentityMock.mockReset()
    fetchIdentityMock.mockResolvedValue('jan.peeters@vives.be')
  })

  it('shows who the person is, as forwarded by Mechatronics', async () => {
    const wrapper = await mountHome()

    expect(wrapper.get('[data-testid="identity"]').text()).toContain('jan.peeters@vives.be')
  })

  it('shows no identity when none was forwarded (e.g. local development)', async () => {
    fetchIdentityMock.mockResolvedValue(null)

    const wrapper = await mountHome()

    expect(wrapper.find('[data-testid="identity"]').exists()).toBe(false)
  })

  it('still renders the page when the identity lookup fails', async () => {
    fetchIdentityMock.mockRejectedValue(new Error('boom'))

    const wrapper = await mountHome()

    expect(wrapper.find('[data-testid="identity"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Media Browser')
  })

  it('links to Media Browser, Analyses, Data Consolidation, and Video Cutting, with no Admin link and no logout', async () => {
    const wrapper = await mountHome()

    const links = wrapper.findAllComponents({ name: 'RouterLink' })
    expect(links.map((link) => link.text())).toEqual([
      'Media Browser',
      'Analyses',
      'Data Consolidation',
      'Video Cutting',
    ])
    expect(wrapper.text()).not.toContain('Admin')
    expect(wrapper.find('button').exists()).toBe(false)
  })
})
