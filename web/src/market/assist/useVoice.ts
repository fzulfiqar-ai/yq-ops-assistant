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

/**
 * v2: `onLevel(0..1)` hears the voice's loudness about 60 times a second while recording (a Web Audio
 * analyser, nothing leaves the phone); the chat writes it into a CSS variable for the orb and the bars, so
 * nothing re-renders per frame. 0 when recording ends.
 */
export function useVoice(onText: (text: string) => void, onLevel?: (level: number) => void) {
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
  const onLevelRef = useRef(onLevel)
  const meter = useRef<{ ctx: AudioContext; raf: number } | null>(null)
  useEffect(() => {
    onTextRef.current = onText
    onLevelRef.current = onLevel
  })

  const stopMeter = useCallback(() => {
    const m = meter.current
    meter.current = null
    if (m) {
      cancelAnimationFrame(m.raf)
      void m.ctx.close().catch(() => {})
    }
    onLevelRef.current?.(0)
  }, [])

  const startMeter = useCallback((stream: MediaStream) => {
    if (!onLevelRef.current) return
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctx) return
    try {
      const ctx = new Ctx()
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 256
      ctx.createMediaStreamSource(stream).connect(analyser)
      const buf = new Uint8Array(analyser.fftSize)
      let smooth = 0
      const tick = () => {
        analyser.getByteTimeDomainData(buf)
        let sum = 0
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128
          sum += v * v
        }
        smooth = smooth * 0.7 + Math.min(1, Math.sqrt(sum / buf.length) * 4) * 0.3
        onLevelRef.current?.(smooth)
        if (meter.current) meter.current.raf = requestAnimationFrame(tick)
      }
      meter.current = { ctx, raf: requestAnimationFrame(tick) }
    } catch {
      /* no meter: the recording works without it */
    }
  }, [])

  const release = useCallback(() => {
    window.clearInterval(timer.current)
    stopMeter()
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
  }, [stopMeter])

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
    startMeter(stream)
    setSecs(0)
    setState('recording')
    const t0 = Date.now()
    timer.current = window.setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000)
      setSecs(s)
      if (s >= MAX_SECONDS) stop()
    }, 250)
  }, [release, startMeter, stop, upload])

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
