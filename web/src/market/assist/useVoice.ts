import { useCallback, useEffect, useRef, useState } from 'react'
import { deviceId } from '../lib/device'
import { postVoice } from './assistApi'
import { recorderMime } from './media'
import { A } from './strings'

/**
 * Tap to record, tap to stop (auto-stop at 55 s), then the clip is sent for transcription and the
 * text goes to `onText`: into the message box for the merchant to check, never sent on its own.
 * The microphone is released the moment recording ends, is cancelled or the panel closes.
 */

export type VoiceState = 'idle' | 'recording' | 'uploading'
const MAX_SECONDS = 55

export function useVoice(onText: (text: string) => void) {
  const [state, setState] = useState<VoiceState>('idle')
  const [secs, setSecs] = useState(0)
  const [note, setNote] = useState('')
  const recRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunks = useRef<Blob[]>([])
  const timer = useRef(0)
  const cancelled = useRef(false)
  const abort = useRef<AbortController | null>(null)
  const onTextRef = useRef(onText)
  useEffect(() => {
    onTextRef.current = onText
  })

  const release = useCallback(() => {
    window.clearInterval(timer.current)
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
  }, [])

  const upload = useCallback(
    async (blob: Blob, ext: string) => {
      setState('uploading')
      const ctrl = new AbortController()
      abort.current = ctrl
      try {
        const res = await postVoice(blob, deviceId(), `voice.${ext}`, ctrl.signal)
        if (ctrl.signal.aborted) return
        if (res.busy) setNote(A.voiceBusy)
        else if (!res.text.trim()) setNote(A.voiceEmpty)
        else onTextRef.current(res.text.trim())
      } catch {
        if (!ctrl.signal.aborted) setNote(A.voiceErr)
      } finally {
        setState('idle')
      }
    },
    [],
  )

  const stop = useCallback(() => {
    const rec = recRef.current
    if (rec && rec.state !== 'inactive') rec.stop()
  }, [])

  const cancel = useCallback(() => {
    cancelled.current = true
    abort.current?.abort()
    const rec = recRef.current
    if (rec && rec.state !== 'inactive') rec.stop()
    release()
    setState('idle')
  }, [release])

  const start = useCallback(async () => {
    setNote('')
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setNote(A.voiceDenied)
      return
    }
    streamRef.current = stream
    const { mime, ext } = recorderMime()
    let rec: MediaRecorder
    try {
      rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
    } catch {
      release()
      setNote(A.voiceErr)
      return
    }
    chunks.current = []
    cancelled.current = false
    rec.ondataavailable = (e) => {
      if (e.data.size) chunks.current.push(e.data)
    }
    rec.onstop = () => {
      release()
      if (cancelled.current) return
      const blob = new Blob(chunks.current, { type: rec.mimeType || mime || 'audio/webm' })
      if (!blob.size) {
        setState('idle')
        setNote(A.voiceEmpty)
        return
      }
      void upload(blob, ext)
    }
    recRef.current = rec
    rec.start()
    setSecs(0)
    setState('recording')
    const t0 = Date.now()
    timer.current = window.setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000)
      setSecs(s)
      if (s >= MAX_SECONDS) stop()
    }, 250)
  }, [release, stop, upload])

  // closing the panel mid-recording lets go of the microphone
  useEffect(
    () => () => {
      cancelled.current = true
      abort.current?.abort()
      const rec = recRef.current
      if (rec && rec.state !== 'inactive') rec.stop()
      release()
    },
    [release],
  )

  return { state, secs, note, start, stop, cancel, clearNote: () => setNote('') }
}
