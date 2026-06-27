export function isPywebview(): boolean {
  return !!(window as any).pywebview
}

export function isDenied(): boolean {
  if (isPywebview()) return false
  return 'Notification' in window && Notification.permission === 'denied'
}

export async function requestPermission(): Promise<boolean> {
  if (isPywebview()) return true
  if (!('Notification' in window)) return false
  if (Notification.permission === 'granted') return true
  if (Notification.permission === 'denied') return false
  const permission = await Notification.requestPermission()
  return permission === 'granted'
}

export function notify(title: string, options?: NotificationOptions): void {
  if (isPywebview()) {
    ;(window as any).pywebview.api.notify(title, options?.body || '')
    return
  }
  if (!('Notification' in window)) return
  if (Notification.permission !== 'granted') return
  try {
    const n = new Notification(title, options)
    n.onclick = () => {
      window.focus()
      n.close()
    }
  } catch {
    // 静默失败
  }
}
