const plugin = require("tailwindcss/plugin")
const fs = require("fs")
const path = require("path")

module.exports = {
  mode: 'jit',
  content: [
    "./js/**/*.js",
    "../lib/*_web.ex",
    "../lib/*_web/**/*.*ex"
  ],
  plugins: [
    require('@tailwindcss/forms'),
    // Allows prefixing tailwind classes with LiveView classes to add rules
    // only when LiveView classes are applied, for example:
    //
    //     <div class="phx-click-loading:animate-ping">
    //
    plugin(({addVariant}) => addVariant("phx-click-loading", [".phx-click-loading&", ".phx-click-loading &"])),
    plugin(({addVariant}) => addVariant("phx-submit-loading", [".phx-submit-loading&", ".phx-submit-loading &"])),
    plugin(({addVariant}) => addVariant("phx-change-loading", [".phx-change-loading&", ".phx-change-loading &"])),

    // Embeds Hero Icons (https://heroicons.com) into your app.css bundle
    // See your `CoreComponents.icon/1` for more information.
    //
    plugin(function({matchComponents, theme}) {
      //let iconsDir = path.join(__dirname, "../priv/hero_icons/optimized")
      let iconsDir = path.join(__dirname, "../assets/vendor/heroicons/optimized")
      let values = {}
      let icons = [
        ["", "/24/outline"],
        ["-solid", "/24/solid"],
        ["-mini", "/20/solid"]
      ]
      icons.forEach(([suffix, dir]) => {
        fs.readdirSync(path.join(iconsDir, dir)).map(file => {
          let name = path.basename(file, ".svg") + suffix
          values[name] = {name, fullPath: path.join(iconsDir, dir, file)}
        })
      })
      matchComponents({
        "hero": ({name, fullPath}) => {
          let content = fs.readFileSync(fullPath).toString().replace(/\r?\n|\r/g, "")
          return {
            [`--hero-${name}`]: `url('data:image/svg+xml;utf8,${content}')`,
            "-webkit-mask": `var(--hero-${name})`,
            "mask": `var(--hero-${name})`,
            "background-color": "currentColor",
            "vertical-align": "middle",
            "display": "inline-block",
            "width": theme("spacing.5"),
            "height": theme("spacing.5")
          }
        }
      }, {values})
    })
  ],
  variants: {
    opacity: ['disabled'],
    cursor: ['disabled', 'hover'],
  },
  theme: {
    extend: {
      inset: {
        '1/2': '50%',
        '1': '100%',
      },
      colors: {
        'ch-blue': '#001a96',
        'ch-gray': '#F0F1F2',
      // from tailwind.ink with ch-blue as the start point
        brand: '#001A96',
        dodgerblue: {
          '50':  '#f4f8fa',
          '100': '#ebf7fa',
          '200': '#c1e4f8',
          '300': '#99d0f7',
          '400': '#5baaf3',
          '500': '#337def',
          '600': '#295be9',
          '700': '#2748d9',
          '800': '#2439ab',
          '900': '#1e3082',
        },
        royalblue: {
          '50':  '#f6f9fa',
          '100': '#f0f7f8',
          '200': '#d6e3f6',
          '300': '#bdcaf4',
          '400': '#909fee',
          '500': '#6673e7',
          '600': '#4f50dc',
          '700': '#443fc8',
          '800': '#39339a',
          '900': '#2b2b74',
        },
        orchid: {
          '50':  '#f9faf9',
          '100': '#f8f7f5',
          '200': '#f2dfec',
          '300': '#ecbce6',
          '400': '#e686d6',
          '500': '#db5ac3',
          '600': '#bf38a3',
          '700': '#a12c8e',
          '800': '#812570',
          '900': '#582053',
        },
        salmon: {
          '50':  '#fbfaf9',
          '100': '#faf7ee',
          '200': '#f8e0d4',
          '300': '#f7beba',
          '400': '#f58a8d',
          '500': '#f35f66',
          '600': '#e33b3c',
          '700': '#c82e37',
          '800': '#a32636',
          '900': '#76202c',
        },
        coral: {
          '50':  '#faf9f8',
          '100': '#faf6eb',
          '200': '#f9e2c5',
          '300': '#f7c49a',
          '400': '#f59663',
          '500': '#f36b3e',
          '600': '#e24620',
          '700': '#c6361f',
          '800': '#9f2c24',
          '900': '#762320',
        },
        chocolate: {
          '50':  '#faf9f7',
          '100': '#f9f6ea',
          '200': '#f8e3bf',
          '300': '#f5ca8c',
          '400': '#f2a054',
          '500': '#ee7732',
          '600': '#d95019',
          '700': '#b93e19',
          '800': '#91311e',
          '900': '#6d271c',
        },
        peru: {
          '50':  '#f9f8f7',
          '100': '#f7f5ed',
          '200': '#f2e7c9',
          '300': '#ead495',
          '400': '#dbb35e',
          '500': '#cb8c3a',
          '600': '#a76620',
          '700': '#83511e',
          '800': '#603d20',
          '900': '#4b301e',
        },
        olivedrab: {
          '50':  '#f6f8f8',
          '100': '#f1f5f1',
          '200': '#e2e9da',
          '300': '#caddaf',
          '400': '#97c47d',
          '500': '#70a257',
          '600': '#518039',
          '700': '#406730',
          '800': '#314d2a',
          '900': '#2c3b26',
        },
        cadetblue: {
          '50':  '#f4f8f8',
          '100': '#ecf6f6',
          '200': '#cce9ee',
          '300': '#a4dbdf',
          '400': '#5fc0c9',
          '500': '#389cb2',
          '600': '#2a7b99',
          '700': '#26637f',
          '800': '#214a5a',
          '900': '#1e3b47',
        },
        cornflowerblue: {
          '50':  '#f4f8fa',
          '100': '#ebf6f9',
          '200': '#c3e6f6',
          '300': '#9ad4f3',
          '400': '#59b1ec',
          '500': '#3287e5',
          '600': '#2865db',
          '700': '#2550c6',
          '800': '#223e94',
          '900': '#1d3370',
        },
      }
    }
  },
  purge: [
    "../**/*.html.eex",
    "../**/*.html.leex",
    "../**/*.html.heex",
    "../**/views/**/*.ex",
    "../**/live/**/*.ex",
    "./js/**/*.js"
  ]
}
