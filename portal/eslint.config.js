import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  // `api/**` is a separate npm project with its own lint — exclude it (and its
  // compiled dist/) from the frontend lint so `eslint .` stays scoped to the SPA.
  { ignores: ["dist/**", "node_modules/**", "api/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
    },
  },
);
