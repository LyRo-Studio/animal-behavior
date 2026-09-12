/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Hogeschool VIVES house style (see CONTEXT.md's styling-deviation
        // decision) — replaces the engineering-standards template palette.
        primary: {
          DEFAULT: '#E00020', // VIVES-rood
          // No brand-mandated hover shade exists (the huisstijlgids is a
          // print/brand guide, not a UI spec) — darkened by mixing in a
          // little zwart, following the same pattern the template's
          // original primary/primary-hover pair used.
          hover: '#C30520',
        },
        // Page background uses zand to break up large empty white areas,
        // per the huisstijlgids; surface (cards/panels) stays pure white
        // so the two tokens are actually visually distinct.
        background: '#EFEEE9', // zand
        surface: '#FFFFFF',
        sand: '#EFEEE9',
        foreground: '#1E1E1E', // zwart
        // muted/border have no VIVES equivalent either; the huisstijlgids
        // explicitly permits only 20/40/60/80% tints of zwart as grayscale
        // exceptions, so those (not arbitrary grays) are used here.
        muted: '#A5A5A5', // 40% zwart
        border: '#D2D2D2', // 20% zwart
        // Documented scoped exception (CONTEXT.md): VIVES has no
        // success/warning/danger equivalents, so the original
        // engineering-standard values are kept.
        success: '#16A34A',
        warning: '#D97706',
        danger: '#DC2626',
      },
      fontFamily: {
        sans: ['Poppins', 'Arial', 'sans-serif'],
        serif: ['Lora', 'serif'],
        caption: ['"Barlow Condensed"', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
