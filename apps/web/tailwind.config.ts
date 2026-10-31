import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#071016",
        panel: "#0d1922",
        thermal: "#ff5a36",
        cyan: "#4eeaff",
        emerald: "#56e39f"
      },
      fontFamily: {
        sans: ["var(--font-noto-thai)", "Inter", "system-ui", "sans-serif"],
        mono: ["var(--font-space-mono)", "ui-monospace", "monospace"]
      },
      boxShadow: { glow: "0 0 24px rgba(78,234,255,.12)" }
    }
  },
  plugins: []
};

export default config;
