/**
 * Photo and voice plumbing, no UI. Photos live in memory only (a Map by message id): sessionStorage
 * keeps a "Photo" placeholder, never the picture.
 */

const photos = new Map<string, string>()

export function keepPhoto(id: string, dataUrl: string): void {
  photos.set(id, dataUrl)
}

export function photoOf(id: string): string | undefined {
  return photos.get(id)
}

export function forgetPhotos(): void {
  photos.clear()
}

const MAX_SIDE = 1280

/** Read a picked file, scale its long side to 1280 px at most and return a JPEG data URL (quality 0.8, ~150-400 KB). */
export async function downscale(file: File): Promise<string> {
  const url = URL.createObjectURL(file)
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error('unreadable image'))
      el.src = url
    })
    const w = img.naturalWidth
    const h = img.naturalHeight
    if (!w || !h) throw new Error('empty image')
    const k = Math.min(1, MAX_SIDE / Math.max(w, h))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(w * k))
    canvas.height = Math.max(1, Math.round(h * k))
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('no canvas')
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    return canvas.toDataURL('image/jpeg', 0.8)
  } finally {
    URL.revokeObjectURL(url)
  }
}

/** Voice needs both getUserMedia and MediaRecorder (older iOS has neither). */
export function canRecord(): boolean {
  return typeof window !== 'undefined' && typeof MediaRecorder !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia)
}

/** The best clip type this browser records: webm on Chrome / Android, mp4 on iOS Safari. */
export function recorderMime(): { mime: string; ext: string } {
  const tries: [string, string][] = [
    ['audio/webm;codecs=opus', 'webm'],
    ['audio/webm', 'webm'],
    ['audio/mp4', 'm4a'],
    ['audio/ogg;codecs=opus', 'ogg'],
  ]
  for (const [mime, ext] of tries) {
    try {
      if (MediaRecorder.isTypeSupported(mime)) return { mime, ext }
    } catch {
      /* try the next */
    }
  }
  return { mime: '', ext: 'webm' }
}
