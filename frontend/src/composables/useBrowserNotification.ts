function isSupported(): boolean {
  return 'Notification' in window
}

export function isDenied(): boolean {
  return isSupported() && Notification.permission === 'denied'
}

export async function requestPermission(): Promise<boolean> {
  if (!isSupported()) return false
  if (Notification.permission === 'granted') return true
  if (Notification.permission === 'denied') return false
  const permission = await Notification.requestPermission()
  return permission === 'granted'
}

export function notify(title: string, options?: NotificationOptions): void {
  if (!isSupported()) return
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
