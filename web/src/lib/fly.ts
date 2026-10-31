// 图片飞行动画：拍照 → 样本栏、样本栏 ↔ 评测大屏共用。
// 只是视觉效果，结果在动画开始前就已经由后端返回；系统设置了减少动态效果时直接跳过。

export const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

export function flyImage(src: string | null | undefined, from: DOMRect | null | undefined, to: DOMRect | null | undefined, ms = 650): Promise<void> {
  if (!src || !from || !to || !from.width || !to.width || reducedMotion()) return Promise.resolve()
  return new Promise((resolve) => {
    const img = document.createElement('img')
    img.src = src
    img.alt = ''
    img.setAttribute('aria-hidden', 'true')
    Object.assign(img.style, {
      position: 'fixed', left: '0', top: '0', margin: '0', objectFit: 'cover', zIndex: '1000',
      pointerEvents: 'none', borderRadius: '4px', boxShadow: '0 12px 32px rgba(15, 20, 17, .35)',
    })
    document.body.appendChild(img)
    const frame = (r: DOMRect) => ({ left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` })
    let settled = false
    const done = () => {
      if (settled) return
      settled = true
      img.remove()
      resolve()
    }
    const anim = img.animate([frame(from), frame(to)], { duration: ms, easing: 'cubic-bezier(.3,.7,.2,1)', fill: 'forwards' })
    anim.onfinish = done
    anim.oncancel = done
    // 后台标签页动画可能不触发回调，流程不能卡在这里
    window.setTimeout(done, ms + 400)
  })
}
