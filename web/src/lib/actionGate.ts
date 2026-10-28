import { useCallback, useState } from 'react'

/**
 * One save at a time per action. The Orders page's Start order, Approve and Advance buttons never disabled
 * while their POST was on its way, so a double click raised two procurement orders. A button disabled from
 * React state is not enough on its own: a second click can land before React has drawn the disabled button,
 * so the gate is checked synchronously, in the handler, before anything is sent.
 *
 * Keys name the thing being saved ("start:VFAN", "approve", "advance:12"), so two different orders can move
 * at once while the same one cannot move twice.
 */
export class ActionGate {
  private running = new Set<string>()

  /** true = this caller may go ahead; false = the same action is already on its way */
  tryStart(key: string): boolean {
    if (this.running.has(key)) return false
    this.running.add(key)
    return true
  }

  finish(key: string): void {
    this.running.delete(key)
  }

  isRunning(key: string): boolean {
    return this.running.has(key)
  }

  snapshot(): ReadonlySet<string> {
    return new Set(this.running)
  }
}

/**
 * Run `fn` unless the same key is already running (then nothing is sent and the result is undefined). The key
 * is freed when `fn` settles, a failure included, so the button works again. `onChange` fires when the key
 * starts and when it ends — the page redraws its buttons from it.
 */
export async function runGuarded<T>(gate: ActionGate, key: string, fn: () => Promise<T>, onChange?: () => void): Promise<T | undefined> {
  if (!gate.tryStart(key)) return undefined
  onChange?.()
  try {
    return await fn()
  } finally {
    gate.finish(key)
    onChange?.()
  }
}

/** The page's gate: `run(key, fn)` sends once; `pending(key)` disables the button while it is on its way. */
export function useActionGate() {
  const [gate] = useState(() => new ActionGate())
  const [running, setRunning] = useState<ReadonlySet<string>>(() => new Set())
  const run = useCallback(
    <T,>(key: string, fn: () => Promise<T>) => runGuarded(gate, key, fn, () => setRunning(gate.snapshot())),
    [gate],
  )
  const pending = useCallback((key: string) => running.has(key), [running])
  return { run, pending }
}
