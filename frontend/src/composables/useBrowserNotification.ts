/**
 * 浏览器桌面通知工具
 * @description 除基础权限申请与 notify 外，提供 notifyOnce(eventId, ...)：
 *              多标签场景下同一事件只弹一次桌面系统通知。
 *
 *              去重口径（用户 2026-10-16 拍板）：**只去重桌面通知**，页内
 *              ElNotification 仍由各标签各自弹出（标签内保持即时反馈）。
 *
 *              实现：所有标签共享 localStorage（同源），以「抢占 + 回验」实现
 *              互斥——写入 claim 后回读校验 token 是否仍是自己的，同源并发写
 *              是串行的，晚写者会覆盖早写者，因此回验失败方判定为落败。
 *              有 navigator.locks（Chromium 系）时用 Web Locks 串行化抢占，
 *              杜绝「同时读到空值 → 双方都写」的竞态；无该 API 时降级为
 *              读写回验（竞态窗口极小，最坏退化为偶发两次通知，不会漏通知）。
 */

const CLAIM_PREFIX = 'browser_notify_claim:'
/** claim 过期时间：同事件重连重放超过该窗口后会再弹一次，60s 足够覆盖重连重放 */
const CLAIM_TTL_MS = 60_000
/** 过期 claim 清理节流间隔（避免每次通知都全量扫 localStorage） */
const SWEEP_INTERVAL_MS = 30_000
let lastSweepAt = 0

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
    // 调用方自定义 onclick（如点击跳转会话）优先；未提供时退化为仅聚焦窗口
    const customOnClick = (options as { onclick?: () => void } | undefined)?.onclick
    n.onclick = () => {
      window.focus()
      n.close()
      customOnClick?.()
    }
  } catch {
    // 静默失败
  }
}

/** 清理已过期的 claim 键（节流执行，防 localStorage 无限堆积） */
function sweepExpiredClaims(): void {
  const now = Date.now()
  if (now - lastSweepAt < SWEEP_INTERVAL_MS) return
  lastSweepAt = now
  try {
    const expired: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (!key || !key.startsWith(CLAIM_PREFIX)) continue
      const raw = localStorage.getItem(key)
      const parsed = raw ? (JSON.parse(raw) as { ts?: number }) : null
      if (!parsed || typeof parsed.ts !== 'number' || now - parsed.ts > CLAIM_TTL_MS) {
        expired.push(key)
      }
    }
    for (const key of expired) localStorage.removeItem(key)
  } catch {
    // localStorage 不可用/配额异常：跳过清理，不影响通知
  }
}

/**
 * 跨标签抢占某事件的「桌面通知权」。
 * @returns true=本标签抢到（应弹通知）；false=别的标签已弹过或本标签落败
 */
function claimOnce(eventId: string): boolean {
  const key = `${CLAIM_PREFIX}${eventId}`
  try {
    const raw = localStorage.getItem(key)
    const parsed = raw ? (JSON.parse(raw) as { ts?: number }) : null
    if (parsed && typeof parsed.ts === 'number' && Date.now() - parsed.ts <= CLAIM_TTL_MS) {
      // 窗口内已被抢占（可能是本标签先前弹过，或别的标签已弹）
      return false
    }
    const token = `${Date.now()}-${Math.random().toString(36).slice(2)}`
    localStorage.setItem(key, JSON.stringify({ token, ts: Date.now() }))
    // 回验：无 Web Locks 时靠同源并发写的串行性判定胜负
    const verify = localStorage.getItem(key)
    return !!verify && (JSON.parse(verify) as { token?: string }).token === token
  } catch {
    // localStorage 不可用（隐私模式等）：不阻塞通知，退化为每次都弹
    return true
  }
}

/**
 * 弹一次桌面通知，且同一 eventId 在全浏览器范围内只弹一次。
 * @param eventId 后端下发的推送唯一标识；缺失时退化为普通 notify（不做去重）
 */
export function notifyOnce(
  eventId: string | undefined,
  title: string,
  options?: NotificationOptions
): void {
  if (!eventId) {
    notify(title, options)
    return
  }
  if (!isSupported() || Notification.permission !== 'granted') return
  sweepExpiredClaims()

  const locks = navigator.locks
  if (locks?.request) {
    // Web Locks 串行化「读-判-写」，消除同源并发抢占竞态
    void locks
      .request(`notify:${eventId}`, () => {
        if (claimOnce(eventId)) notify(title, options)
      })
      .catch(() => {
        // 锁服务异常：退化为直接抢占，保证通知不丢
        if (claimOnce(eventId)) notify(title, options)
      })
    return
  }
  if (claimOnce(eventId)) notify(title, options)
}