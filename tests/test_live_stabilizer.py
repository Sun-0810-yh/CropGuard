# -*- coding: utf-8 -*-
"""实时框稳定器：留框、保持门槛、换物清框、最佳帧、一物一框（纯逻辑，不开相机不跑模型）。

现场问题是实物置信度卡线、框闪一下就没、拍不到带框图。这些用例锁住修复的口径。
"""
import ast
import os
import sys

import numpy as np

from _registry import test_case

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.live_stabilizer import LiveStabilizer, dedupe_boxes, keep_conf   # noqa: E402

BOX = [100.0, 100.0, 300.0, 300.0]


def _frame(value=40, h=480, w=640):
    return np.full((h, w, 3), value, dtype=np.uint8)


def _det(conf, box=None, name='moth'):
    return [name, conf, list(box or BOX)]


# ==================== 一物一框 ====================
@test_case
def test_dedupe_two_classes_on_one_object_keeps_one():
    got = dedupe_boxes([_det(0.3, name='a'), _det(0.6, [105, 105, 305, 305], name='b')])
    assert len(got) == 1 and got[0][0] == 'b', got


@test_case
def test_dedupe_keeps_two_separate_objects():
    got = dedupe_boxes([_det(0.5), _det(0.4, [400, 100, 600, 300], name='b')])
    assert len(got) == 2, got


@test_case
def test_dedupe_drops_small_box_inside_big_box():
    got = dedupe_boxes([_det(0.7), _det(0.3, [150, 150, 200, 200], name='b')])
    assert len(got) == 1 and got[0][1] == 0.7, got


# ==================== 留框 / 门槛 ====================
@test_case
def test_keep_conf_is_lower_than_show_conf_with_floor():
    assert abs(keep_conf(0.4) - 0.24) < 1e-9
    assert keep_conf(0.1) == 0.1


@test_case
def test_box_needs_show_conf_to_appear_then_holds_on_low_conf():
    st = LiveStabilizer()
    f = _frame()
    assert st.update(f, [_det(0.2)], 0.0, 0.25) == [], '低于出框门槛不能出框'
    assert len(st.update(f, [_det(0.3)], 0.1, 0.25)) == 1, '过线要出框'
    assert len(st.update(f, [_det(0.16)], 0.2, 0.25)) == 1, '已有框在保持门槛上要留住'


@test_case
def test_box_survives_short_miss_and_expires_after_hold():
    st = LiveStabilizer(hold_sec=1.0)
    f = _frame()
    st.update(f, [_det(0.5)], 0.0, 0.25)
    assert len(st.update(f, [], 0.9, 0.25)) == 1, '漏检不到 1 秒框要在'
    assert st.update(f, [], 1.2, 0.25) == [], '超过 1 秒要消失'


@test_case
def test_removed_object_clears_early():
    st = LiveStabilizer(hold_sec=1.0)
    st.update(_frame(40), [_det(0.5)], 0.0, 0.25)
    assert len(st.update(_frame(200), [], 0.2, 0.25)) == 1, '宽限期内还在（物体可能只是在动）'
    assert st.update(_frame(200), [], 0.5, 0.25) == [], '框内画面变了要提前清框'


@test_case
def test_moving_object_never_gets_two_boxes():
    st = LiveStabilizer()
    f = _frame()
    st.update(f, [_det(0.5)], 0.0, 0.25)
    got = st.update(f, [_det(0.5, [290, 100, 490, 300])], 0.1, 0.25)
    assert len(got) == 1, f'物体快速移动时旧框不能留成第二个框：{got}'
    assert got[0][2][0] > 150, got


@test_case
def test_two_objects_keep_two_boxes():
    st = LiveStabilizer()
    got = st.update(_frame(), [_det(0.5), _det(0.4, [400, 100, 600, 300], name='b')], 0.0, 0.25)
    assert len(got) == 2, got


@test_case
def test_class_is_voted_not_flipping():
    st = LiveStabilizer()
    f = _frame()
    for i in range(4):
        st.update(f, [_det(0.5, name='a')], i * 0.1, 0.25)
    got = st.update(f, [_det(0.3, name='b')], 0.5, 0.25)
    assert got[0][0] == 'a', got


# ==================== 看到框就能拍到框 ====================
@test_case
def test_take_best_returns_highest_conf_frame_then_clears():
    st = LiveStabilizer()
    f = _frame()
    st.update(f, [_det(0.3)], 0.0, 0.25)
    st.update(f, [_det(0.7)], 0.1, 0.25)
    st.update(f, [_det(0.4)], 0.2, 0.25)
    picked = st.take_best(0.3, f)
    assert picked and picked[1][0][1] == 0.7, picked
    assert st.take_best(0.3, f) is None, '缓存用过要清空，下一张不能再用上一张'


@test_case
def test_take_best_skips_frame_of_previous_object():
    st = LiveStabilizer()
    st.update(_frame(40), [_det(0.7)], 0.0, 0.25)
    assert st.take_best(0.1, _frame(200)) is None, '画面已换，绝不能把上一只记给这一只'


@test_case
def test_best_cache_clears_when_no_box_on_screen():
    st = LiveStabilizer(hold_sec=1.0)
    f = _frame()
    st.update(f, [_det(0.5)], 0.0, 0.25)
    st.update(f, [], 1.2, 0.25)
    assert st.take_best(1.2, f) is None, '屏幕上没框时不能拿缓存充数'


# ==================== 管路 ====================
def _source(rel):
    with open(os.path.join(_ROOT, rel), 'r', encoding='utf-8') as f:
        return f.read()


@test_case
def test_linkage_only_sees_config_threshold_raw_detections():
    """低门槛结果和留住的框都不能触发喷淋/语音。"""
    src = _source('app/inference.py')
    tree = ast.parse(src)
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    verdict = ast.get_source_segment(src, funcs['_linkage_verdict'])
    assert 'conf_thres' in verdict, verdict
    step = ast.get_source_segment(src, funcs['_live_step'])
    assert '_linkage_verdict(formatted' in step, '联动必须用原始检测，不能用稳定框'
    pump = ast.get_source_segment(src, funcs['_infer_pump'])
    assert '_linkage, level, strong' in pump, pump


@test_case
def test_capture_endpoints_registered():
    src = _source('api_server.py')
    assert '"/api/eval/capture"' in src and '"/api/eval/live_conf"' in src


@test_case
def test_eval_flow_keeps_undetected_out_of_strip():
    src = _source('web/src/views/WorkbenchView.vue')
    i = src.index('async function evalCapture')
    body = src[i:src.index('async function flyToStrip')]
    assert body.index('!r.detections.length') < body.index('samples.value.push'), '未检出必须在入栏之前拦下'
    assert 'startTimer()' in body, '计时要从第一张落位开始'
    assert 'session.start = performance.now()' not in body, '评分作业不能在点拍照时开始计时'


@test_case
def test_review_page_has_no_rejudge_and_uses_live_box_style():
    """评判后不可更改；可以自由回看；大屏用实时画面同一个组件叠框。"""
    src = _source('web/src/views/EvalReviewView.vue')
    assert 'rejudge' not in src, '改判功能已删除'
    assert 's.verdict || phase.value' in src, '判过的样本必须拒绝再次判定'
    assert 'DetectionStage' in src and ':src="onStage.url"' in src, '大屏要用原图 + DetectionStage 叠框'
    assert 'function view(' in src, '要能回看，不必先判当前这张'
    work = _source('web/src/views/WorkbenchView.vue')
    assert "stageSample?.counts && mode !== 'eval'" in work, '拍照页不做判分，只回看'
    assert "mode.value === 'eval') return" in work, '拍照页的 Y/N 快捷键不能判分'
    i = work.index('class="review-bar"')
    assert 'goReview' in work[i:work.index('</div>', i)], '回看缩略图时也要有去评测入口'
