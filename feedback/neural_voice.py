# -*- coding: utf-8 -*-
"""神经网络语音（edge-tts 晓晓）+ 离线缓存 + Windows MCI 播放。

为什么这样做：
- SAPI / System.Speech 的中文女声很机械；edge-tts 的神经音色自然得多，但**要联网**，
  而比赛要求离线运行 → 播报文案都是固定模板（害虫名 × 风险等级 × 喷淋动作），
  联网时用 `scripts/build_voice_cache.py` 把所有句子预先合成成 mp3，存在 feedback/voice_cache/，
  现场离线直接放缓存。
- 缓存里没有的句子：联网就现合成（顺便写进缓存），断网就交给旧的 System.Speech 兜底，**绝不静默**。
- 播放用 winmm 的 MCI（Windows 自带，能直接放 mp3，零依赖、无窗口）。
"""
import asyncio
import ctypes
import hashlib
import os
import threading
from pathlib import Path

VOICE = os.environ.get('CG_TTS_VOICE', 'zh-CN-XiaoxiaoNeural')
RATE = '+0%'
CACHE_DIR = Path(__file__).resolve().parent / 'voice_cache'
_SYNTH_TIMEOUT = 8.0
_mci_lock = threading.Lock()


def _norm(text):
    return str(text or '').strip()


def cache_path(text, voice=VOICE, rate=RATE):
    key = hashlib.sha1(f'{voice}|{rate}|{_norm(text)}'.encode('utf-8')).hexdigest()[:20]
    return CACHE_DIR / f'{key}.mp3'


def is_cached(text):
    p = cache_path(text)
    return p.exists() and p.stat().st_size > 0


def synthesize(text, voice=VOICE, rate=RATE, timeout=_SYNTH_TIMEOUT):
    """联网合成一句写进缓存；成功返回路径，失败抛异常。"""
    import edge_tts   # 延迟导入：没装也不影响离线放缓存
    out = cache_path(text, voice, rate)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix('.part')

    async def _run():
        await asyncio.wait_for(edge_tts.Communicate(_norm(text), voice, rate=rate).save(str(tmp)),
                               timeout=timeout)

    # 本机系统代理已失效但会被自动读取 → 合成时绕开
    os.environ.setdefault('NO_PROXY', '*')
    asyncio.run(_run())
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError('edge-tts 没有返回音频')
    os.replace(tmp, out)
    return out


def _mci(cmd):
    buf = ctypes.create_unicode_buffer(256)
    err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, 0)
    if err:
        msg = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciGetErrorStringW(err, msg, 255)
        raise RuntimeError(f'MCI {err}: {msg.value}')
    return buf.value


def play_file(path, volume=1.0):
    """同步播放一个 mp3（放完才返回）。"""
    if os.name != 'nt':
        raise RuntimeError('MCI 播放只支持 Windows')
    alias = f'cg{threading.get_ident()}'
    with _mci_lock:
        _mci(f'open "{os.path.abspath(path)}" type mpegvideo alias {alias}')
    try:
        try:
            _mci(f'setaudio {alias} volume to {int(max(0.0, min(1.0, float(volume))) * 1000)}')
        except Exception:
            pass
        _mci(f'play {alias} wait')
    finally:
        try:
            _mci(f'close {alias}')
        except Exception:
            pass


def available():
    """MCI 可用 + （有缓存 或 装了 edge-tts）才算这个后端能用。"""
    if os.name != 'nt':
        return False, '非 Windows'
    try:
        ctypes.windll.winmm  # noqa: B018
    except Exception as exc:
        return False, f'winmm 不可用: {exc}'
    has_cache = CACHE_DIR.exists() and any(CACHE_DIR.glob('*.mp3'))
    if has_cache:
        return True, ''
    try:
        import edge_tts  # noqa: F401
        return True, ''
    except Exception:
        return False, '没有语音缓存，也没装 edge-tts'
