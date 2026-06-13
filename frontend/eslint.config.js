export default [
  {
    ignores: ["dist/**", "node_modules/**", "coverage/**", "*.min.js"],
  },
  {
    files: ["**/*.{js,mjs,cjs,jsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": "warn",
    },
  },
];
