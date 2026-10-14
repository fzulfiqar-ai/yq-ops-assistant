import { useEffect, useRef, useState } from 'react'

/**
 * Voice typing for a short note (the Spotted sheet, release r8m): the browser's own Web Speech API,
 * as the AI Assistant's mic does (pages/Assistant.tsx) — en-US or ar-SA, interim results, one
 * utterance per tap. Nothing is recorded or uploaded by the app; the browser turns speech into text.
 * Where the browser has no recognizer (Firefox, some in-app browsers) `supported` is false and the
 * sheet simply shows no mic.
 */

export type DictationLang = 'en-US' | 'ar-SA'

interface SpeechAlternative {
  transcript: string
}
interface SpeechResultList {
  length: number
  [index: number]: { 0: SpeechAlternative; isFinal?: boolean }
}
interface SpeechRec {
  lang: string
  interimResults: boolean
  continuous: boolean
  onresult: ((e: { results: SpeechResultList }) => void) | null
  onend: (() => void) | null
  onerror: (() => void) | null
  start(): void
  stop(): void
  abort?(): void
}
type SpeechRecCtor = new () => SpeechRec

function recognizer(): SpeechRecCtor | null {
  if (typeof window === 'undefined') return null
  const w = window as unknown as { SpeechRecognition?: SpeechRecCtor; webkitSpeechRecognition?: SpeechRecCtor }
  return w.SpeechRecognition || w.webkitSpeechRecognition || null
}

export function useDictation() {
  const recRef = useRef<SpeechRec | null>(null)
  const [listening, setListening] = useState(false)

  // a sheet that closes mid-sentence stops listening
  useEffect(
    () => () => {
      try {
        recRef.current?.abort?.()
      } catch {
        /* already stopped */
      }
      recRef.current = null
    },
    [],
  )

  function stop() {
    try {
      recRef.current?.stop()
    } catch {
      /* already stopped */
    }
    setListening(false)
  }

  /** Start listening; `onText` receives the whole utterance so far each time it grows. */
  function start(lang: DictationLang, onText: (text: string) => void): boolean {
    const Ctor = recognizer()
    if (!Ctor) return false
    try {
      recRef.current?.abort?.()
    } catch {
      /* nothing to stop */
    }
    const rec = new Ctor()
    rec.lang = lang
    rec.interimResults = true
    rec.continuous = false
    rec.onresult = (e) => {
      let text = ''
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript
      onText(text.trim())
    }
    const done = () => {
      if (recRef.current === rec) {
        recRef.current = null
        setListening(false)
      }
    }
    rec.onend = done
    rec.onerror = done
    recRef.current = rec
    try {
      rec.start()
      setListening(true)
      return true
    } catch {
      recRef.current = null
      setListening(false)
      return false
    }
  }

  return { supported: recognizer() !== null, listening, start, stop }
}
