/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        'tg-bg': '#0d1117',
        'tg-surface': '#161b22',
        'tg-border': '#30363d',
        'tg-text': '#e6edf3',
        'tg-muted': '#8b949e',
        'tg-blue': '#58a6ff',
        'tg-green': '#3fb950',
        'tg-yellow': '#d29922',
        'tg-red': '#f85149',
        'tg-orange': '#f0883e',
        'tg-purple': '#bc8cff',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
}
