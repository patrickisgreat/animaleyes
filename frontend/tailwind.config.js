/** @type {import('tailwindcss').Config} */
// Colors are CSS variables (space-separated RGB channels) so themes swap at runtime via the
// [data-theme] attribute while Tailwind's /opacity modifiers keep working.
const v = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ash: v("ash"), teal: v("teal"), sage: v("sage"), pearl: v("pearl"), beige: v("beige"),
        bg: v("bg"), surface: v("surface"), surface2: v("surface2"), edge: v("edge"),
        ink: v("ink"), muted: v("muted"), bad: v("bad"),
      },
      borderRadius: { xl2: "14px" },
      fontFamily: { sans: ['"Lato"', "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"] },
    },
  },
  plugins: [],
};
