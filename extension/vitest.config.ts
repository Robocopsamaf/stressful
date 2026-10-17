import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // parseXml needs a real DOMParser.
    environment: "jsdom",
    include: ["test/**/*.test.ts"],
  },
});
