/**
 * Brands that wear their own look on a promotion (components/SlideCard, market.css "A brand's own
 * promotion"): the brand's wordmark, its colours and, where it is a written fact, the "Exclusive in
 * Bahrain" seal. A campaign the office schedules wears it when EVERY product it shows is that brand's
 * (lib/slides campaignSlide), so the office switches it on and off from the Campaigns desk like any
 * campaign; nothing here decides what is shown, only how it looks.
 *
 * WEKOME (15-Nov-2026): YQ Bahrain W.L.L is WEKOME's exclusive distributor in Bahrain (the brand's
 * certificate, issued 1-Aug-2026, valid one year), so `exclusive` is a fact, not a slogan. The wordmark
 * is the certificate's, white on transparent (public/brands/wekome.png, 742 x 160). The colours are the
 * brand's own: black and white with an orange accent (its price list).
 */

export interface BrandTheme {
  /** the catalog's brand, upper case (ShopItem.brand) */
  key: string
  logo: string
  logoWidth: number
  logoHeight: number
  /** YQ holds the brand exclusively in Bahrain: the kicker says so and the seal shows */
  exclusive: boolean
}

export const BRAND_THEMES: Readonly<Record<string, BrandTheme>> = {
  WEKOME: { key: 'WEKOME', logo: '/brands/wekome.png', logoWidth: 742, logoHeight: 160, exclusive: true },
}

/** The theme of a picture set every item of which is one themed brand's, else null (an empty set: null). */
export function themedBrand(items: ReadonlyArray<{ brand?: string | null }>): BrandTheme | null {
  if (!items.length) return null
  const key = (items[0].brand || '').trim().toUpperCase()
  const theme = key ? BRAND_THEMES[key] : undefined
  if (!theme) return null
  return items.every((i) => (i.brand || '').trim().toUpperCase() === key) ? theme : null
}
