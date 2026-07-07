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
          bg: "#15130f",
          surface: "#1e1b16",
          raised: "#252019",
          ink: "#e8e2d4",
          dim: "#a89e8c",
          faint: "#6b6355",
          rule: "#3a352c",
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
