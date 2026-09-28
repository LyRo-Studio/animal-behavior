import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ConfirmDialog from '../ConfirmDialog.vue'

function mountDialog() {
  return mount(ConfirmDialog, {
    props: { title: 'Cuts already exist', confirmLabel: 'Re-cut' },
    slots: { default: '<p>T001 already has Cuts.</p>' },
    attachTo: document.body,
  })
}

describe('ConfirmDialog', () => {
  it('is a labelled modal dialog showing its message', () => {
    const wrapper = mountDialog()

    const dialog = wrapper.find('[role="dialog"]')
    expect(dialog.attributes('aria-modal')).toBe('true')
    const titleId = dialog.attributes('aria-labelledby')!
    expect(wrapper.find(`#${titleId}`).text()).toBe('Cuts already exist')
    expect(dialog.text()).toContain('T001 already has Cuts.')
    wrapper.unmount()
  })

  it('confirms only when the confirm button is pressed', async () => {
    const wrapper = mountDialog()

    await wrapper.find('[data-testid="confirm-dialog-confirm"]').trigger('click')

    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(wrapper.emitted('cancel')).toBeUndefined()
    wrapper.unmount()
  })

  it('cancels from the cancel button or Escape', async () => {
    const wrapper = mountDialog()

    await wrapper.find('[data-testid="confirm-dialog-cancel"]').trigger('click')
    await wrapper.find('[role="dialog"]').trigger('keydown', { key: 'Escape' })

    expect(wrapper.emitted('cancel')).toHaveLength(2)
    expect(wrapper.emitted('confirm')).toBeUndefined()
    wrapper.unmount()
  })

  it('starts on the safe choice, so Enter alone never confirms', () => {
    const wrapper = mountDialog()

    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="confirm-dialog-cancel"]').element,
    )
    wrapper.unmount()
  })
})
