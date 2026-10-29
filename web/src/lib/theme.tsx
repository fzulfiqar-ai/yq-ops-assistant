import { createContext, useContext, useLayoutEffect, useState, type ReactNode } from 'react'

type Theme = 'light' | 'dark'

const ThemeCtx = createContext<{ theme: Theme; toggle: () => void } | undefined>(undefined)

/** 'dark' or the default light — the rule src/boot/portal-boot.js applies before the first paint. */
function savedTheme(): Theme {
  try {
    return localStorage.getItem('yq-theme') === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(savedTheme)
  // Before the browser paints (a layout effect): the served first frame already carries the saved
  // theme (portal-boot.js sets html[data-theme] ahead of the first paint), so a dark user never sees a
  // light frame; this keeps <html> equal to it, and applies a toggle in the same frame.
  useLayoutEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try {
      localStorage.setItem('yq-theme', theme)
    } catch {
      /* ignore */
    }
  }, [theme])
  const toggle = () => setTheme((t) => (t === 'light' ? 'dark' : 'light'))
  return <ThemeCtx.Provider value={{ theme, toggle }}>{children}</ThemeCtx.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme() {
  const v = useContext(ThemeCtx)
  if (!v) throw new Error('useTheme must be used within ThemeProvider')
  return v
}
