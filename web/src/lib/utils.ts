import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// tailwind.config.js's custom size text-kpi: the default merge takes it for a colour and drops it beside a real
// colour, so every coloured KPI figure fell back to the body size (text-2xs already reads as a size)
const twMerge = extendTailwindMerge({ extend: { classGroups: { 'font-size': [{ text: ['kpi'] }] } } })

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
