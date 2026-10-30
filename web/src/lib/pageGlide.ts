import { DURATION, ENTER_EASE } from './motionOnce'

/**
 * The page glide's numbers (lib/pageFrame PageGlide), on the motion system's own (lib/motionOnce.ts):
 * the 200 ms fade on the enter curve, with an 8 px rise; reduced motion gets the fade alone, in
 * 120 ms. Transform and opacity only. Pure, so web/scripts/portal_shell_test.mjs can hold them.
 */
export const GLIDE_RISE_PX = 8
export const GLIDE_MS = Math.round(DURATION.fade * 1000)
export const GLIDE_CALM_MS = 120
export const GLIDE_EASE = `cubic-bezier(${ENTER_EASE.join(',')})`

export function glideFrames(calm: boolean): { keyframes: Keyframe[]; options: KeyframeAnimationOptions } {
  if (calm) {
    return { keyframes: [{ opacity: 0 }, { opacity: 1 }], options: { duration: GLIDE_CALM_MS, easing: 'linear' } }
  }
  return {
    keyframes: [
      { opacity: 0, transform: `translateY(${GLIDE_RISE_PX}px)` },
      { opacity: 1, transform: 'none' },
    ],
    options: { duration: GLIDE_MS, easing: GLIDE_EASE },
  }
}
