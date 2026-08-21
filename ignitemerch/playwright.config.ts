import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3005",
    reuseExistingServer: true,
  },
  use: {
    baseURL: "http://localhost:3005",
    ...devices["Desktop Chrome"],
  },
});
