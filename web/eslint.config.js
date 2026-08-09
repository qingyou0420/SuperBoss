import js from '@eslint/js'
import prettier from 'eslint-config-prettier'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'
import vueParser from 'vue-eslint-parser'

export default tseslint.config(
    {
        ignores: ['dist', 'node_modules', 'coverage'],
    },
    js.configs.recommended,
    ...pluginVue.configs['flat/essential'],
    ...tseslint.configs.recommended,
    {
        files: ['**/*.vue'],
        languageOptions: {
            parser: vueParser,
            parserOptions: {
                parser: tseslint.parser,
            },
        },
    },
    prettier,
)
