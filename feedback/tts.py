# -*- coding: utf-8 -*-
"""离线语音播报引擎（TTS）。

## 为什么不用 pyttsx3 的默认调用方式

现场问题是「只有第一条有语音，后面检测都不出声」。根因不在节流，而在线程模型：

* `feedback.py` 原来在**主线程** `pyttsx3.init()` 创建 SAPI5 引擎，
  却把 `runAndWait()` 放到**每次新起的线程**里调用；
* pyttsx3 2.99 的 SAPI5 驱动靠 COM 事件回调（`SAPI5DriverEventSink`）来
  清 `DriverProxy._busy`（见 `pyttsx3/driver.py::setBusy/_pump`）。
  COM 接口是在创建它的线程/单元（apartment）里封送回调的，**换个线程调用
  就收不到事件** → `_busy` 永远为 True → 队列再也不 `_pump()` → 后面全哑；
* 再叠加 `_speak_lock`：第一个线程卡在 `runAndWait()` 里不返回，锁永不释放；
* 而且 `except Exception: pass` 把报错全吞了，现场完全看不到原因。

## 本模块的做法

1. **专用常驻线程**：该线程内 `CoInitialize()` + 建引擎 + 跑消息泵，
   所有播报都投递到这个线程执行 —— COM 单元始终一致，事件回调不再跨线程；
2. **多后端兜底**（全部离线，按顺序探测，选中哪个写日志）：
   * `win32com` 直连 `SAPI.SPVoice`（最稳，无需 pyttsx3）；
   * `pyttsx3`（专用线程 + `startLoop(False)` + `iterate()` 泵消息）；
   * `powershell` 调 `System.Speech.Synthesis.SpeechSynthesizer`（子进程，兜底）；
   * `none`（没声卡/没引擎）：只记日志，绝不影响检测主流程。
3. **永远非阻塞**：`speak()` 只入队就返回；队列只留最新一句（旧句丢弃），
   不积压、不串音；`status()` 暴露运行状态给 `/api/health` 与 GUI 日志。

对外只暴露 `TTSEngine`，`feedback/feedback.py` 是唯一使用者。
"""
import os
import queue
import re
import subprocess
import threading
import time

# ---------------------------------------------------------------- 可选依赖
try:
    import pythoncom
except Exception:                                   # 非 Windows / 无 pywin32
    pythoncom = None

try:
    import pyttsx3
except Exception:
    pyttsx3 = None

try:
    import win32com.client as win32com_client
except Exception:
    win32com_client = None


_POWERSHELL = None


def _find_powershell():
    """找 powershell.exe（兜底后端用），只找一次。"""
    global _POWERSHELL
    if _POWERSHELL is not None:
        return _POWERSHELL or None
    for name in ('powershell.exe', 'pwsh.exe'):
        for base in (os.environ.get('SystemRoot', r'C:\Windows'),
                     os.environ.get('ProgramFiles', r'C:\Program Files')):
            cand = os.path.join(base, 'System32', 'WindowsPowerShell', 'v1.0', name)
            if os.path.exists(cand):
                _POWERSHELL = cand
                return cand
    _POWERSHELL = ''
    return None


# ---------------------------------------------------------------- 让声音更像人
_ZH_HINTS = ('chinese', 'zh-cn', 'zh_cn', 'huihui', 'yaoyao', 'kangkang',
             'xiaoxiao', 'yunxi', 'hanhan', 'tracy', '中文', '普通话')
_SENT_SPLIT = re.compile(r'(?<=[。！？!?；;\n])')


def wpm_to_sapi(rate):
    """把配置里的"字/分钟"换算成 SAPI Rate（-10~10）。

    旧换算 `(rate-100)/10` 会把默认 180 变成 +8（接近最大语速，即"超高速"）。
    新换算以 150 字/分钟 ≈ 0（SAPI 正常语速），每 20 字/分钟一档：
    130 → -1（略缓、更像人在讲话），180 → +2。
    """
    try:
        return max(-10, min(10, int(round((float(rate) - 150) / 20.0))))
    except Exception:
        return 0


def split_sentences(text):
    """按句末标点切成短句，句间留自然停顿；过短的碎片并回前一句。"""
    parts = [p.strip() for p in _SENT_SPLIT.split(str(text)) if p and p.strip()]
    merged = []
    for p in parts:
        if merged and len(p) < 4:
            merged[-1] += p
        else:
            merged.append(p)
    return merged or [str(text).strip()]


# ================================================================ 后端实现
class _Win32ComBackend:
    """直接驱动 SAPI.SPVoice（进程内、离线、同步阻塞由调用线程负责）。

    ⚠️ 注意：`Dispatch('SAPI.SPVoice')` 本身不会检验语音链路，
    有的机器上新建对象成功、真正 `Speak()` 时才报 COM 错
    （实测本机就是 `-2147200966` SPERR_* 系列）。
    所以这里实例化时先**空播一句**做真实校验，失败就抛错让 TTSEngine 换下一个后端。
    """

    name = 'win32com'

    def __init__(self):
        if win32com_client is None:
            raise RuntimeError('pywin32 未安装')
        self._voice = win32com_client.Dispatch('SAPI.SPVoice')
        try:
            self._voice.Speak('', 0)      # 空串：只校验语音对象能不能用
        except Exception as exc:
            raise RuntimeError(f'SAPI 语音对象不可用: {type(exc).__name__}: {str(exc)[:80]}')
        self._pick_chinese_voice()

    def _pick_chinese_voice(self):
        """优先选中文（最好是 Huihui/Xiaoxiao 这类自然音色），选不到就保持系统默认。"""
        try:
            tokens = self._voice.GetVoices()
            for i in range(tokens.Count):
                tk = tokens.Item(i)
                desc = (tk.GetDescription() or '').lower()
                if any(h in desc for h in _ZH_HINTS):
                    self._voice.Voice = tk
                    return
        except Exception:
            pass

    def set_rate(self, rate):
        try:
            self._voice.Rate = wpm_to_sapi(rate)
        except Exception:
            pass

    def set_volume(self, volume):
        try:
            self._voice.Volume = max(0, min(100, int(float(volume) * 100)))
        except Exception:
            pass

    def speak(self, text):
        self._voice.Speak(text, 0)          # 0 = 同步，说完才返回


class _Pyttsx3Backend:
    """pyttsx3 引擎：只在专用线程里创建/使用，用 iterate() 泵消息。

    注意：`runAndWait()` 会自己开循环，容易和外部线程/锁打架，
    这里改用 `startLoop(False)` + 轮询 `iterate()` 的写法。
    """

    name = 'pyttsx3'

    def __init__(self):
        if pyttsx3 is None:
            raise RuntimeError('pyttsx3 未安装')
        if pythoncom is not None:
            pythoncom.CoInitialize()
        self._engine = pyttsx3.init()
        self._iterating = False
        try:
            for v in self._engine.getProperty('voices'):
                blob = f"{getattr(v, 'name', '')} {getattr(v, 'id', '')} {getattr(v, 'languages', '')}".lower()
                if any(h in blob for h in _ZH_HINTS):
                    self._engine.setProperty('voice', v.id)
                    break
        except Exception:
            pass

    def set_rate(self, rate):
        try:
            self._engine.setProperty('rate', int(min(float(rate), 175)))
        except Exception:
            pass

    def set_volume(self, volume):
        try:
            self._engine.setProperty('volume', max(0.0, min(1.0, float(volume))))
        except Exception:
            pass

    def speak(self, text):
        self._engine.say(text)
        if not self._iterating:
            try:
                self._engine.startLoop(False)
                self._iterating = True
            except Exception:
                self._iterating = False
        if self._iterating:
            deadline = time.time() + 30
            while self._engine.isBusy() and time.time() < deadline:
                try:
                    self._engine.iterate()
                except Exception:
                    break
                time.sleep(0.02)
            try:
                self._engine.endLoop()
            except Exception:
                pass
            self._iterating = False
        else:
            self._engine.runAndWait()


class _PowerShellBackend:
    """兜底：起一个 powershell 子进程念一句（离线 SAPI）。"""

    name = 'powershell'

    def __init__(self):
        if _find_powershell() is None:
            raise RuntimeError('找不到 powershell.exe')
        self._rate = 0
        self._volume = 100

    def set_rate(self, rate):
        self._rate = wpm_to_sapi(rate)

    def set_volume(self, volume):
        self._volume = max(0, min(100, int(float(volume) * 100)))

    def speak(self, text):
        self.speak_sentences([str(text)], 0.0)

    def speak_sentences(self, sentences, pause_sec):
        """一个子进程念完所有句子，句间用 Start-Sleep 停顿（避免每句重启 powershell）。"""
        lines = []
        for i, sent in enumerate(sentences):
            safe = str(sent).replace("'", "''")
            lines.append(f"$s.Speak('{safe}');")
            if i < len(sentences) - 1 and pause_sec > 0:
                lines.append(f"Start-Sleep -Milliseconds {int(pause_sec * 1000)};")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "try { $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Female, "
            "[System.Speech.Synthesis.VoiceAge]::Adult, 0, "
            "[System.Globalization.CultureInfo]::GetCultureInfo('zh-CN')) } catch {}; "
            f"$s.Rate = {self._rate}; $s.Volume = {self._volume}; "
            + " ".join(lines)
        )
        creationflags = 0x08000000 if os.name == 'nt' else 0   # 不弹窗
        subprocess.run([_find_powershell(), '-NoProfile', '-NonInteractive',
                        '-Command', script],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60, creationflags=creationflags)


class _NeuralBackend:
    """神经网络音色（edge-tts 晓晓，见 feedback/neural_voice.py）。

    句子有缓存 → 离线直接放；没缓存 → 联网现合成并写缓存；
    断网又没缓存 → 这一句交给 System.Speech 念（音色会变，但绝不静默）。
    """

    name = 'neural'

    def __init__(self):
        from feedback import neural_voice
        ok, why = neural_voice.available()
        if not ok:
            raise RuntimeError(why)
        self._nv = neural_voice
        self._volume = 1.0
        self._fallback = None
        self.fallback_count = 0

    def set_rate(self, rate):
        pass   # 神经音色用合成时的固定语速，SAPI 的字/分钟不适用

    def set_volume(self, volume):
        self._volume = max(0.0, min(1.0, float(volume)))

    def _fallback_backend(self):
        if self._fallback is None:
            self._fallback = _PowerShellBackend()
            self._fallback.set_volume(self._volume)
        return self._fallback

    def speak(self, text):
        if not str(text or '').strip():
            return   # 构建时的空播校验：available() 已检查过 MCI 与缓存
        self.speak_sentences([text], 0.0)

    def speak_sentences(self, sentences, pause_sec):
        for i, sent in enumerate(sentences):
            path = self._nv.cache_path(sent)
            if not self._nv.is_cached(sent):
                try:
                    path = self._nv.synthesize(sent)
                except Exception:
                    path = None
            if path is not None:
                self._nv.play_file(path, self._volume)
            else:
                self.fallback_count += 1
                self._fallback_backend().speak(sent)
            if i < len(sentences) - 1 and pause_sec > 0:
                time.sleep(pause_sec)


# ================================================================ 后端注册表
class BackendRegistry:
    """管理可选 TTS 后端的优先级、实例化与真实链路校验。

    把原先分散在 `_backend_classes` / `_build_backend` /
    `TTSEngine._fallback_after_selftest` 里的逻辑集中到这里，
    新增后端时只要改 `_BACKENDS` 列表即可。
    """

    _BACKENDS = [
        ('neural', _NeuralBackend),
        ('win32com', _Win32ComBackend),
        ('pyttsx3', _Pyttsx3Backend),
        ('powershell', _PowerShellBackend),
    ]

    def __init__(self, prefer='auto'):
        self.prefer = (prefer or 'auto').strip().lower()
        self._order = list(self._BACKENDS)
        if self.prefer != 'auto':
            self._order = self._reorder(self.prefer)

    def _reorder(self, prefer):
        """把指定后端移到最前，其余保持原顺序。"""
        matched = []
        rest = []
        for name, cls in self._BACKENDS:
            if name == prefer:
                matched.append((name, cls))
            else:
                rest.append((name, cls))
        return matched + rest

    def build(self, rate, volume, selftest=True):
        """逐个尝试后端：初始化 → 设置参数 → 真实播报校验。

        返回 (backend, backend_name, errors)。
        如果全部失败，backend 为 None，backend_name 为 'none'。
        """
        errors = []
        for name, cls in self._order:
            try:
                be = cls()
            except Exception as exc:
                errors.append(f'{name}: 初始化失败 {type(exc).__name__}: {str(exc)[:120]}')
                continue
            try:
                be.set_rate(rate)
                be.set_volume(volume)
                if selftest:
                    be.speak('')                       # 真实链路校验
            except Exception as exc:
                errors.append(f'{name}: 校验失败 {type(exc).__name__}: {str(exc)[:100]}')
                continue
            return be, name, errors
        return None, 'none', errors


def _build_backend(prefer='auto', rate=150, volume=1.0, selftest=True):
    """后端构建入口（模块级，返回 (backend, backend_name, errors)）。

    这是 TTSEngine 线程里实际调用的构建缝（2026-10-16 补回）：

    * 新实现委托给 ``BackendRegistry``（保持"优先级 + 真出声校验"的设计）；
    * 同时它也是**测试注入点** —— tests/test_feedback_and_camera.py 通过
      替换本函数来塞入假后端，从而在不需要真实声卡的机器上验证
      "队列 / 最新优先 / 去重" 这些核心逻辑。

    没有这层缝时，那 7 条语音用例会以 AttributeError 挂掉（曾发生）。
    """
    return BackendRegistry(prefer).build(rate, volume, selftest=selftest)


# ================================================================ 对外引擎
class TTSEngine:
    """专用线程 + 队列的离线播报器；任何失败都不会抛出到调用方。"""

    def __init__(self, enabled=True, engine='auto', rate=150, volume=1.0,
                 dedup_sec=0.5, init_selftest=True, pause_sec=0.25):
        self.enabled = bool(enabled) and engine != 'none'
        self.engine_pref = engine or 'auto'
        self.rate = rate
        self.volume = volume
        self.dedup_sec = float(dedup_sec or 0)
        self.init_selftest = bool(init_selftest)
        self.pause_sec = float(pause_sec)

        self._queue = queue.Queue(maxsize=4)
        self._thread = None
        self._running = False
        self._backend = None
        self._backend_name = 'none'
        self._init_errors = []
        self._ready = threading.Event()
        self._spoken = 0
        self._failed = 0
        self._dropped = 0
        self._last_text = ''
        self._last_say_ts = 0.0
        self._lock = threading.Lock()

        if not self.enabled:
            self._init_errors.append('disabled by config')
            return
        self._start_thread()

    # ------------------------------------------------------------ 线程
    def _start_thread(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, name='TTS', daemon=True)
        self._thread.start()
        # 等后端就绪（最多 5 秒），这样启动日志能直接报出选中了哪个引擎
        self._ready.wait(timeout=5.0)

    def _loop(self):
        """专用线程：建引擎 + 串行消费队列。所有 COM 调用都发生在这个线程。"""
        if pythoncom is not None:
            try:
                pythoncom.CoInitialize()
            except Exception:
                pass

        # 注意：这里走模块级 _build_backend（而不是 registry.build 直接调用），
        # 让测试可以注入假后端；两者行为完全一致。
        self._backend, self._backend_name, self._init_errors = _build_backend(
            self.engine_pref, self.rate, self.volume, selftest=self.init_selftest)

        if self._backend is None:
            print(f"[TTS] 无可用语音后端（{'; '.join(self._init_errors) or 'none'}）；"
                  f"语音播报将静默跳过", flush=True)
        else:
            print(f'[TTS] 后端就绪 engine={self._backend_name} '
                  f'errors={self._init_errors or []}', flush=True)
        self._ready.set()

        while True:
            try:
                text = self._queue.get(timeout=0.2)
            except queue.Empty:
                if not self._running:
                    break
                continue
            if text is None:
                break
            if self._backend is None:
                self._failed += 1
                continue
            t0 = time.time()
            try:
                self._speak_naturally(text)
                self._spoken += 1
                print(f"[TTS] say ok ({time.time() - t0:.2f}s) {text}", flush=True)
            except Exception as exc:
                self._failed += 1
                print(f'[TTS] say FAILED {type(exc).__name__}: {str(exc)[:160]}', flush=True)

        if pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _speak_naturally(self, text):
        """逐句念并在句间留停顿；念到一半来了新句子就不再念剩下的（最新优先）。"""
        sentences = split_sentences(text)
        batch = getattr(self._backend, 'speak_sentences', None)
        if batch is not None:               # 子进程型后端：整段一次念完
            batch(sentences, self.pause_sec)
            return
        for i, sent in enumerate(sentences):
            self._backend.speak(sent)
            if i < len(sentences) - 1:
                if not self._queue.empty():
                    break
                time.sleep(self.pause_sec)

    # ------------------------------------------------------------ 对外
    @staticmethod
    def wait_backend(engine, timeout=5.0):
        """等后端就绪；就绪返回 True（供启动自检使用）。"""
        if engine is None:
            return False
        engine._ready.wait(timeout=timeout)
        return engine._backend is not None

    @property
    def backend_name(self):
        return self._backend_name

    def speak(self, text, flush=True):
        """入队播报（立即返回）。

        flush=True 时清掉尚未播出的旧句（"最新一句优先"），避免连续检测时排队背诵。
        返回 True 表示已入队。
        """
        if not self.enabled or not text:
            return False
        text = str(text).strip()
        if not text:
            return False
        now = time.time()
        with self._lock:
            if self.dedup_sec and text == self._last_text and now - self._last_say_ts < self.dedup_sec:
                return False
            self._last_text = text
            self._last_say_ts = now
        if flush:
            while True:
                try:
                    self._queue.get_nowait()
                    self._dropped += 1
                except queue.Empty:
                    break
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            self._dropped += 1
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(text)
            except Exception:
                return False
        return True

    def status(self):
        return {
            'enabled': self.enabled,
            'engine': self._backend_name,
            'ready': self._backend is not None,
            'spoken': self._spoken,
            'failed': self._failed,
            'dropped': self._dropped,
            'queue': self._queue.qsize(),
            'init_errors': list(self._init_errors),
        }

    def close(self):
        self._running = False
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None


# ---------------------------------------------------------------- 自检
def selftest(engine='auto', phrase='语音播报自检，株得已就绪'):
    """跑一次自检：建引擎 → 念一句 → 返回 (ok, status)。

    给 `api_server.py` / `main.py` 启动日志用：现场一眼就能看到"到底响不响"。
    """
    tts = TTSEngine(enabled=True, engine=engine)
    deadline = time.time() + 8
    while time.time() < deadline and not tts.status()['ready'] and tts.status()['engine'] == 'none':
        time.sleep(0.1)
    ok = tts.speak(phrase, flush=True)
    # 等它真的念完（最长 10 秒），便于日志给出确定的结论
    deadline = time.time() + 10
    while time.time() < deadline:
        st = tts.status()
        if st['spoken'] or st['failed']:
            break
        time.sleep(0.1)
    st = tts.status()
    ok = bool(st['spoken'])
    tts.close()
    return ok, st
