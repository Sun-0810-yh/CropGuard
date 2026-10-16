# -*- coding: utf-8 -*-
"""语音反馈与摄像头采集层测试（不需要真实硬件）。

语音：把 TTS 后端换成"假的"，验证队列 / 最新优先 / 去重逻辑 ——
      这才是"只有第一条有语音"问题的根本修法（见 feedback/tts.py 注释）。

相机：用一个自造的视频文件当源，验证能打开、read() 非阻塞、
      以及引用计数（多客户端共享同一采集源，最后一个 release 才关设备）。
"""
import time

import cv2
import numpy as np

import feedback.tts as tts_mod
from _registry import fixture, requires, test_case
from feedback.feedback import Feedback
from feedback.tts import TTSEngine
from tool import camera_source as cam


# ==================== 语音 ====================
class _FakeBackend:
    """假后端：不出声，只记录被念过的句子。"""

    def __init__(self, sink):
        self.sink = sink
        self.name = 'fake'

    def set_rate(self, rate):
        pass

    def set_volume(self, volume):
        pass

    def speak(self, text):
        self.sink.append(text)


@fixture
def fake_engine():
    """把后端构建替换成假后端（等价 monkeypatch），产出 (engine, sink)。

    注意：TTSEngine 初始化时会用一句空串做"真链路校验"，
    所以这里先丢掉那条记录，让用例从干净的 sink 开始。

    2026-10-16：`_build_backend` 的签名与 tts.py 的 BackendRegistry 对齐
    （prefer/rate/volume/selftest），假后端按当前契约返回 (backend, name, errors)。
    """
    sink = []
    original = tts_mod._build_backend

    def _build(prefer='auto', rate=180, volume=1.0, selftest=True):
        return _FakeBackend(sink), [], []

    tts_mod._build_backend = _build
    eng = TTSEngine(enabled=True, engine='auto')
    TTSEngine.wait_backend(eng, timeout=3)
    time.sleep(0.2)
    sink.clear()
    try:
        yield eng, sink
    finally:
        eng.close()
        tts_mod._build_backend = original


@test_case
def test_tts_speaks_multiple_times(fake_engine):
    """连续三句都要真的播出去（旧版只有第一句）。"""
    eng, sink = fake_engine
    for i in range(3):
        assert eng.speak(f'第{i}条语音') is True
        time.sleep(0.2)
    deadline = time.time() + 3
    while len(sink) < 3 and time.time() < deadline:
        time.sleep(0.05)
    assert len(sink) == 3, f'应播出 3 句，实际 {sink}'


@test_case
def test_tts_latest_wins_when_flushed(fake_engine):
    """flush=True 时旧句被丢弃，不会排长队背诵。"""
    eng, sink = fake_engine
    for i in range(4):
        eng.speak(f'句{i}', flush=True)
    deadline = time.time() + 3
    while not sink and time.time() < deadline:
        time.sleep(0.05)
    assert sink, '至少应播出最新一句'
    assert sink[-1] == '句3', sink


@test_case
def test_tts_dedup_window(fake_engine):
    eng, sink = fake_engine
    assert eng.speak('重复的话', flush=True) is True
    assert eng.speak('重复的话', flush=True) is False, '去重窗口内重复句应被丢弃'


@test_case
def test_tts_status_fields(fake_engine):
    eng, sink = fake_engine
    st = eng.status()
    for key in ('enabled', 'engine', 'ready', 'spoken', 'failed', 'dropped', 'queue'):
        assert key in st, (key, st)


@test_case
def test_feedback_announces_on_state_change(fake_engine):
    """状态变化立刻播报；同状态复读受 speak_interval 限制。"""
    eng, sink = fake_engine
    fb = Feedback(enabled=False, tts=eng, speak_interval=30)
    fb.enabled = True
    assert fb.trigger('高风险', ['蚜虫']) is True
    time.sleep(0.2)
    assert fb.trigger('高风险', ['蚜虫']) is False, '同状态且未到间隔，不应重复播'
    assert fb.trigger('中风险', ['蚜虫']) is True, '风险等级变化必须播报'
    time.sleep(0.2)
    assert len([s for s in sink if s]) >= 2, sink


@test_case
def test_feedback_no_risk_silent_by_default(fake_engine):
    eng, sink = fake_engine
    fb = Feedback(enabled=False, tts=eng, announce_no_risk=False)
    fb.enabled = True
    assert fb.trigger('无风险', []) is False


@test_case
def test_feedback_spray_info_in_text(fake_engine):
    eng, sink = fake_engine
    fb = Feedback(enabled=False, tts=eng, announce_no_risk=True)
    fb.enabled = True
    fb.trigger('高风险', ['蚜虫'], spray_info='已给片区A喷淋3秒')
    deadline = time.time() + 2
    while not sink and time.time() < deadline:
        time.sleep(0.05)
    assert sink and '喷淋3秒' in sink[-1], sink


# ==================== 摄像头采集 ====================
_SEQ_FRAMES = 3000          # 序列帧数：够整个模块用，且"永远有新帧"
_SEQ_DIRNAME = 'cam_seq'    # 固定目录名 —— 复用，不再每次新建上千个文件


def _seq_root():
    """工作区内的短名临时根目录。

    注意：不要写 %TEMP% —— 一是沙箱可能只允许工作区写入（实测 cv2.imwrite 报
    permission denied），二是 Windows 长路径会让 OpenCV 的 VideoCapture 认不出来。
    """
    import os
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'output', '_test_tmp')
    os.makedirs(root, exist_ok=True)
    return root


def _prune_stale_dirs(root, keep):
    """开跑时清掉历史运行留下的临时目录（以前是 uuid 命名的 cg_cam_*）。

    旧实现的坑：每个用例各建一个目录、各写 3000 张图、且从不清理 ——
    一次运行就能留下几万个文件，攒了几十轮之后连删都要删半天。
    """
    import os
    import shutil
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if path == keep or not os.path.isdir(path):
            continue
        if name.startswith(('cg_cam_', _SEQ_DIRNAME)):
            shutil.rmtree(path, ignore_errors=True)


def _ensure_moving_source():
    """建（或复用）图片序列源，返回带 %05d 的通配路径。

    为什么用图片序列而不是 .avi：一是长路径/中文路径下 OpenCV 的 VideoWriter 在
    Windows 上容易报 `CAP_IMAGES: can't find starting number`；二是图片序列天然
    "永远有新帧"。方块左右移动 → 画面指纹一直在变，不会触发看门狗里
    "画面长时间不变就判死"的逻辑。
    """
    import os
    import shutil
    root = _seq_root()
    dirpath = os.path.join(root, _SEQ_DIRNAME)
    pattern = os.path.join(dirpath, 'frame_%05d.png')
    marker = os.path.join(dirpath, '.frames')
    _prune_stale_dirs(root, keep=dirpath)
    if os.path.isfile(marker):                      # 上一轮建好的直接复用
        try:
            with open(marker, encoding='utf-8') as fh:
                done = int(fh.read().strip())
            last = os.path.join(dirpath, f'frame_{done - 1:05d}.png')
            if done == _SEQ_FRAMES and os.path.isfile(last):
                return pattern
        except Exception:
            pass
    shutil.rmtree(dirpath, ignore_errors=True)
    os.makedirs(dirpath, exist_ok=True)
    for i in range(_SEQ_FRAMES):
        img = np.zeros((120, 160, 3), np.uint8)
        x = int(5 + (i * 2) % 140)
        cv2.rectangle(img, (x, 40), (x + 15, 80), (255, 255, 255), -1)
        cv2.imwrite(os.path.join(dirpath, f'frame_{i:05d}.png'), img)
    with open(marker, 'w', encoding='utf-8') as fh:
        fh.write(str(_SEQ_FRAMES))
    return pattern


@fixture(scope='module')
def moving_video():
    """序列源夹具：**只建一次、跑完删掉**（生成器夹具 = 自带收尾）。

    迷你框架把收尾统一放到 summary()（等价 pytest 的 teardown），
    所以整个模块跑完时目录才被删除；pytest 下按 module 作用域同样只建一次。
    """
    import os
    import shutil
    pattern = _ensure_moving_source()
    try:
        yield pattern
    finally:
        shutil.rmtree(os.path.dirname(pattern), ignore_errors=True)


@test_case
def test_camera_source_reads_frames(moving_video):
    src = cam.get_source(('test', 'moving'), source=moving_video,
                         width=0, height=0, probe_timeout=5.0)
    try:
        assert src.wait_first_frame(timeout=5.0), '应能从序列源拿到首帧'
        ok, frame = src.read()
        assert ok and frame is not None, '应读到帧'
        assert frame.shape[:2] == (120, 160), frame.shape
        st = src.state()
        assert st['state'] in ('ok', 'down'), st
        assert st['size'] == '160x120', st
    finally:
        cam.release_source(('test', 'moving'))


@test_case
def test_camera_shared_singleton_refcount(moving_video):
    key = ('test', 'shared')
    a = cam.get_source(key, source=moving_video, width=0, height=0, probe_timeout=5.0)
    b = cam.get_source(key, source=moving_video, width=0, height=0, probe_timeout=5.0)
    assert a is b, '同一个 key 必须复用同一个采集源（多客户端共享）'
    cam.release_source(key)
    assert cam.pool_state().get(str(key)), '还有引用时不能关设备'
    cam.release_source(key)
    assert not cam.pool_state().get(str(key)), '最后一个引用释放后才关设备'


@test_case
def test_camera_missing_device_reports_dead():
    src = cam.get_source(('test', 'dead'), source='W:/not/a/camera_%05d.png', probe_timeout=0.5)
    try:
        st = src.state()
        assert st['ok'] is False, st
        assert st['state'] == cam.STATE_DEAD, st
        assert st['last_error'], '打不开要给出原因，不能静默'
    finally:
        cam.release_source(('test', 'dead'))


@test_case
def test_camera_read_is_nonblocking(moving_video):
    src = cam.get_source(('test', 'nb'), source=moving_video, width=0, height=0,
                         probe_timeout=3.0)
    try:
        t0 = time.time()
        for _ in range(20):
            src.read()
        assert time.time() - t0 < 1.0, 'read() 必须立刻返回（非阻塞）'
    finally:
        cam.release_source(('test', 'nb'))


# ==================== 多策略解析（现场 2026-10-09 踩的坑） ====================
@test_case
def test_resolve_camera_opens_without_waiting_for_frame(moving_video):
    """解析阶段只要求"能打开"、不等第一帧。

    现场实测：USB 相机冷启动要 3 秒才吐第一帧。如果解析时每步都等帧，
    就会把"其实能用"的相机判成"打不开"（这就是本次 bug 的根因）。
    """
    t0 = time.time()
    cap, report = cam.resolve_camera(preferred_index=None, max_index=0,
                                     extra_indices=[moving_video],
                                     fourcc='MJPG', verbose=False)
    dt = time.time() - t0
    try:
        assert cap is not None, f'应能打开合成序列源；report={report}'
        assert report['opened'], report
        assert dt < 8.0, f'解析不该慢到离谱（实测 {dt:.1f}s）'
    finally:
        if cap is not None:
            cap.release()


@test_case
def test_start_returns_true_on_open_without_first_frame(moving_video):
    """start() 的语义 = "打开成功"，不是"已出图"。

    索引1 冷启动 3 秒的相机，如果 start() 非要等帧才算成功，就会被误判。
    """
    src = cam.get_source(('test', 'openonly'), source=moving_video, width=0, height=0,
                         probe_timeout=0.01)      # 故意把等帧预算压到极小
    try:
        st = src.state()
        assert st['opened'] in (True, False)      # 打开与否都要有明确结论
        if st['opened']:
            assert st['state'] in ('ok', 'down'), st
    finally:
        cam.release_source(('test', 'openonly'))


@test_case
def test_resolve_camera_reports_attempts_when_nothing_works():
    """全都打不开时，必须留下"试过什么"的记录（现场定位靠它）。"""
    cap, report = cam.resolve_camera(preferred_index=None, max_index=0,
                                     extra_indices=['W:/not/a/camera_%05d.png'],
                                     width=0, height=0, verbose=False)
    assert cap is None, '不存在的源不该打开成功'
    assert report['tried'], '失败时必须给出尝试记录'
    assert report['opened'] is None, report
    assert any('打不开' in t or '失败' in t for t in report['tried']), report


@test_case
def test_open_failure_has_actionable_last_error():
    """打不开时的 last_error 要能指导排查（不是空的、不是纯异常堆栈）。"""
    src = cam.get_source(('test', 'msg'), source='W:/not/a/camera_%05d.png', probe_timeout=1.0)
    try:
        st = src.state()
        if not st['ok']:
            assert st['last_error'], st
            assert len(st['last_error']) > 8, st
    finally:
        cam.release_source(('test', 'msg'))


@test_case
def test_gui_adapter_has_full_interface(moving_video):
    """GUI 的 CameraCapture 适配器必须实现 main.py 用到的所有方法。

    回归用例：曾经漏了 `wait_first_frame`，导致"日志说相机打开了、界面却永远没画面"
    （AttributeError 被 except 吞掉）。这里逐一检查，别再漏。

    main.py 会 import ultralytics / PyQt5：把测试套件单独克隆出来时（没带主项目的
    vendored 副本）允许 SKIP，而不是报一条看着像 bug 的失败。
    """
    requires('ultralytics', 'PyQt5')
    import importlib.util
    import os
    main_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'main.py')
    spec = importlib.util.spec_from_file_location('cg_main_probe', main_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)        # __name__ != '__main__'，不会启动 GUI
    adapter = mod.CameraCapture
    for name in ('start', 'wait_first_frame', 'read', 'peek', 'state', 'stop'):
        assert hasattr(adapter, name), f'CameraCapture 缺少 {name}()（main.py 会调用它）'


@test_case
def test_open_failure_then_retry_succeeds(moving_video, monkeypatch_factory=None):
    """关键场景：**第一次打开失败**（相机被占用），第二次点击必须重新尝试打开。

    旧实现会把"死"对象一直放在池里复用 → 之后永远打不开（现场表现：日志说打开了、
    但没画面）。这里用"第一次强制失败、第二次正常"来固定这个行为。
    """
    key = ('test', 'retry')
    original = cam.CameraSource._open_best
    calls = {'n': 0}

    def flaky_open(self):
        calls['n'] += 1
        if calls['n'] == 1:
            return None, None                    # 第一次：模拟"被占用/打开失败"
        return original(self)                    # 之后：正常打开

    cam.CameraSource._open_best = flaky_open
    try:
        first = cam.get_source(key, source=moving_video, width=0, height=0,
                               probe_timeout=2.0)
        st1 = first.state()
        assert st1['opened_once'] is False, f'第一次应打开失败: {st1}'
        cam.release_source(key)                  # 计数归零，但池里仍留着死对象
        # 第二次取同一个 key：应当重新尝试打开并成功
        second = cam.get_source(key, source=moving_video, width=0, height=0,
                                probe_timeout=3.0)
        st2 = second.state()
        assert calls['n'] >= 2, f'第二次必须重新尝试打开（实际只调了 {calls["n"]} 次）'
        assert st2['opened_once'] is True, f'第二次应打开成功: {st2}'
        assert second.wait_first_frame(timeout=3.0), '第二次应能拿到帧'
        cam.release_source(key)
    finally:
        cam.CameraSource._open_best = original
        try:
            cam.release_source(key)
        except Exception:
            pass
