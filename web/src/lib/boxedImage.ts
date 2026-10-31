// 把后端（GPU）返回的检测结果画到原图上，样式与实时画面的 DetectionStage 一致。
// 只改"怎么画"：框、类别、置信度都原样来自后端，这里不做任何判断。
// 缩略图、飞行动画、落盘快照用它；大屏直接用 DetectionStage 叠框。
import type { Detection } from './api'
import { latinName } from './format'

const REF_WIDTH = 1000      // 实时画面约 1000px 宽时框 2px、字 12.5px，按图宽等比缩放

export async function renderBoxed(rawUrl: string, detections: Detection[]): Promise<string | null> {
  const img = new Image()
  img.src = rawUrl
  try { await img.decode() } catch { return null }
  const canvas = document.createElement('canvas')
  canvas.width = img.naturalWidth
  canvas.height = img.naturalHeight
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.drawImage(img, 0, 0)

  const k = img.naturalWidth / REF_WIDTH
  const css = getComputedStyle(document.documentElement)
  const signal = css.getPropertyValue('--signal').trim() || '#E8C547'
  const fontFamily = getComputedStyle(document.body).fontFamily || 'sans-serif'
  const fontSize = 12.5 * k
  const labelH = 24 * k
  const padX = 8 * k
  ctx.font = `700 ${fontSize}px ${fontFamily}`
  ctx.textBaseline = 'middle'
  ctx.lineWidth = 2 * k
  for (const d of detections) {
    const [x1, y1, x2, y2] = d.bbox
    ctx.strokeStyle = signal
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)
    const text = `${d.label_en || latinName(d.class_name)} ${d.confidence.toFixed(2)}`
    const w = ctx.measureText(text).width + padX * 2
    // 框贴着上沿时标签放进框内，避免被裁掉（与 DetectionStage 同规则）
    const top = y1 / img.naturalHeight < 0.07 ? y1 : y1 - labelH
    ctx.fillStyle = signal
    ctx.fillRect(x1 - k, top, w, labelH)
    ctx.fillStyle = '#16201A'
    ctx.fillText(text, x1 - k + padX, top + labelH / 2)
  }
  return canvas.toDataURL('image/jpeg', 0.9)
}
