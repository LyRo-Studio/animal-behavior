import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ConsolidationStatusBadge from '../ConsolidationStatusBadge.vue'

describe('ConsolidationStatusBadge', () => {
  it.each([
    ['processing', 'Processing…'],
    ['completed', 'Completed'],
    ['failed', 'Failed'],
  ] as const)('labels %s as %s', (status, label) => {
    const wrapper = mount(ConsolidationStatusBadge, { props: { status } })

    expect(wrapper.text()).toBe(label)
  })
})
