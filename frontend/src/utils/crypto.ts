import { sha256 as _sha256 } from 'js-sha256'

export function sha256(text: string): string {
  return _sha256(text)
}
