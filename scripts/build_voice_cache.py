# -*- coding: utf-8 -*-
"""联网预合成所有播报句子（神经音色），存到 feedback/voice_cache/，现场离线直接放。

用法:  python scripts/build_voice_cache.py           # 只补缺的
       python scripts/build_voice_cache.py --play    # 合成完放一句试听

覆盖：高/中/低风险 × 喷淋动作（A/B/所有区域 × 1~5 秒）、无风险句、自检句。
播报不念害虫名，句子是有限集合，全部预合成；缓存外的句子联网现合成，断网由 System.Speech 兜底。
改了 feedback/feedback.py 的文案后要重跑。
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('NO_PROXY', '*')

from feedback import neural_voice  # noqa: E402
from feedback.feedback import _speak_text, spray_phrase  # noqa: E402
from feedback.tts import split_sentences  # noqa: E402


def all_sentences():
    out = []
    sprays = [''] + [spray_phrase(g, d) for g in ('all', 'g1', 'g2') for d in range(1, 6)]
    for lvl in ('高风险', '中风险', '低风险'):
        for sp in sprays:
            out += split_sentences(_speak_text(lvl, [], sp))
    out += split_sentences(_speak_text('无风险', []))
    out += ['语音播报自检，株得已就绪']
    return list(dict.fromkeys(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--play', action='store_true')
    ap.add_argument('--prune', action='store_true', help='删掉当前文案用不到的旧缓存')
    args = ap.parse_args()
    sents = all_sentences()
    todo = [s for s in sents if not neural_voice.is_cached(s)]
    print(f'共 {len(sents)} 句，缺 {len(todo)} 句，音色 {neural_voice.VOICE}')
    failed = 0
    for i, s in enumerate(todo, 1):
        try:
            neural_voice.synthesize(s, timeout=20)
        except Exception as exc:
            failed += 1
            print(f'  [失败] {s}: {type(exc).__name__} {exc}')
        if i % 25 == 0:
            print(f'  {i}/{len(todo)}', flush=True)
    print(f'完成，失败 {failed} 句；缓存目录 {neural_voice.CACHE_DIR}')
    if args.prune:
        keep = {neural_voice.cache_path(s).name for s in sents}
        stale = [p for p in neural_voice.CACHE_DIR.glob('*.mp3') if p.name not in keep]
        for p in stale:
            p.unlink()
        print(f'清理旧缓存 {len(stale)} 个')
    if args.play:
        neural_voice.play_file(neural_voice.cache_path(sents[0]))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
