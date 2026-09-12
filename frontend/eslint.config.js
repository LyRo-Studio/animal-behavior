import js from '@eslint/js'
import eslintConfigPrettier from 'eslint-config-prettier'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
      },
    },
  },
  {
    rules: {
      // Single-word page/view component names (e.g. `App.vue`) are fine here.
      'vue/multi-word-component-names': 'off',
    },
  },
  // Must stay last: turns off every stylistic rule that fights Prettier,
  // which owns formatting (see `format`/`npm run format`).
  eslintConfigPrettier,
)
