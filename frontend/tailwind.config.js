/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Archivo", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "Space Mono", "monospace"],
      },
      colors: {
        ledger: {
          bg: "#141b28",
          surface: "#1c2433",
          raised: "#232c3f",
          ink: "#e8e2d4",
          dim: "#8696b0",
          faint: "#56637b",
          rule: "#333f55",
          accent: "#a8320c",
          amber: "#c17f2e",
        },
        provider: {
          anthropic: "#b8935a",
          openai: "#6b8f7a",
          gemini: "#7288a3",
        },
      },
      keyframes: {
        "ledger-roll": {
          "0%": { transform: "translateY(0.35em)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
      },
      animation: {
        "ledger-roll": "ledger-roll 0.3s ease-out",
      },
    },
  },
  plugins: [],
}
