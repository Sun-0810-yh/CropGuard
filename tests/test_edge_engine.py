# -*- coding: utf-8 -*-
"""EdgeJev 部署适配层测试（不需要模型即可跑核心逻辑）。

覆盖三类：
1. 纯映射：EdgeJev answers -> 项目指令契约（含危险词/片区/钳制）；
2. 合并策略：规则优先 + 置信度闸门兜底（安全类拒绝永不被翻案）；
3. 状态与降级：模型未部署时如实报告（带构建指引），不静默假装。

另带一条"真模型"冒烟用例：只有 onnxruntime 与 edge_models/jev-int8 都在时才跑，
否则 SKIP（CI 上没模型不算失败）。
"""
import os
import sys

from _registry import SkipTest, requires, test_case

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import edge_engine as ee   # noqa: E402


# ---------------- 构造假 answers ----------------
def _choice_result(choice, probs, conf=0.9):
    return {'type': 'choice', 'choice': choice, 'probabilities': probs, 'confidence': conf}


def _noul(v):
    return {'type': 'noul', 'noul': v, 'confidence': 0.8}


def _score(v, probs=None):
    return {'type': 'score', 'score': v, 'legend': {}, 'probabilities': probs or {}, 'confidence': 0.8}


def _answers(intent='spray', ip=0.95, group='g1', gp=0.9, unsafe=0.02, sec=1.0):
    return {
        'intent': _choice_result(intent, {'spray': ip if intent != 'spray' else ip,
                                          'stop': 1 - ip, 'other': 0.01}),
        'group': _choice_result(group, {group: gp, 'none': 1 - gp}),
        'unsafe': _noul(unsafe),
        'seconds': _score(sec),
    }


def _fake_duration(capped=3, raw=3):
    def _pd(text, default=3, max_duration=5):
        return capped, raw
    return _pd


# ==================== 纯映射 ====================
@test_case
def test_map_stop_intent():
    r = ee.map_command_answers('把泵关了吧', _answers(intent='stop'),
                               max_duration=5, block_words=[])
    assert r['intent'] == 'stop' and r['valid'] and r['group'] == 'all', r
    assert r['needs_confirm'] is True


@test_case
def test_map_spray_with_group_and_clamp_note():
    r = ee.map_command_answers('给片区A喷100秒', _answers(group='g1'),
                               max_duration=5, parse_duration=_fake_duration(5, 100),
                               block_words=[])
    assert r['intent'] == 'spray' and r['valid'], r
    assert r['group'] == 'g1' and r['duration'] == 5, r
    assert '钳制' in r['reply'], r


@test_case
def test_map_block_word_refused():
    r = ee.map_command_answers('给片区A加大剂量多喷5秒', _answers(group='g1'),
                               max_duration=5, block_words=['加大剂量', '多喷'])
    assert not r['valid'] and '不安全' in r['reason'], r


@test_case
def test_map_missing_group_rejected():
    r = ee.map_command_answers('帮我喷100秒', _answers(group='none', gp=0.99),
                               max_duration=5, block_words=[])
    assert not r['valid'] and r['group'] is None and '片区' in r['reply'], r


@test_case
def test_map_other_intent():
    r = ee.map_command_answers('今天天气怎么样', _answers(intent='other'),
                               max_duration=5, block_words=[])
    assert not r['valid'] and '没有识别到' in r['reply'], r


# ==================== 合并策略（规则优先 + 闸门兜底） ====================
def _rule(valid=False, reason='', intent='unknown', group=None, duration=None, reply=''):
    return {'intent': intent, 'group': group, 'duration': duration, 'valid': valid,
            'needs_confirm': True, 'max_duration': 5, 'reason': reason, 'reply': reply}


@test_case
def test_combine_rule_valid_wins():
    rule = _rule(valid=True, intent='spray', group='g1', duration=3, reply='规则来')
    out = ee.combine_command(rule, {'success': True, 'answers': _answers(group='g2')},
                             '给片区A喷3秒', max_duration=5)
    assert out['valid'] and out['group'] == 'g1' and out['engine'] == 'rule', out
    assert out['reply'] == '规则来'


@test_case
def test_combine_safety_refusal_never_rescued():
    """含危险词的规则拒绝：EdgeJev 说得再好也不能翻案。"""
    rule = _rule(valid=False, reason='含不安全表述"加大剂量"', intent='spray')
    edge = {'success': True, 'answers': _answers(group='g1')}
    out = ee.combine_command(rule, edge, '加大剂量喷洒', max_duration=5,
                             block_words=['加大剂量'])
    assert not out['valid'] and out['engine'] == 'rule', out
    assert '不安全' in out['reason']


@test_case
def test_combine_rescue_spray_when_rules_unknown():
    """规则没识别出来，EdgeJev 高置信喷洒 -> 兜底成有效解析（仍需人工确认）。"""
    rule = _rule(valid=False, reason='没有识别到喷洒类指令')
    edge = {'success': True, 'answers': _answers(intent='spray', ip=0.9, group='g2', gp=0.8)}
    out = ee.combine_command(rule, edge, '把2号地块浇一下', max_duration=5,
                             parse_duration=_fake_duration(3, 3), block_words=[])
    assert out['valid'] and out['engine'] == 'edge', out
    assert out['group'] == 'g2' and out['duration'] == 3, out
    assert out['needs_confirm'] is True


@test_case
def test_combine_rescue_group_gate():
    """意图可信但片区没给 -> 回"未指定片区"，不允许猜一个。"""
    rule = _rule(valid=False, reason='没有识别到喷洒类指令')
    edge = {'success': True, 'answers': _answers(intent='spray', ip=0.9, group='none', gp=0.95)}
    out = ee.combine_command(rule, edge, '开一下水', max_duration=5, block_words=[])
    assert not out['valid'] and out['group'] is None and '片区' in out['reply'], out


@test_case
def test_combine_stop_rescue_requires_high_confidence():
    """停止兜底闸门：0.75 不够（会被"再来两秒"式的误判穿过），0.93 放行。"""
    rule = _rule(valid=False, reason='没有识别到喷洒类指令')
    low = {'success': True, 'answers': _answers(intent='stop', ip=0.75)}
    out = ee.combine_command(rule, low, 'B区再来两秒吧', max_duration=5, block_words=[])
    assert not out['valid'] and out['engine'] == 'rule', out

    high = {'success': True, 'answers': _answers(intent='stop', ip=0.93)}
    out2 = ee.combine_command(rule, high, '把泵关了吧', max_duration=5, block_words=[])
    assert out2['valid'] and out2['intent'] == 'stop' and out2['engine'] == 'edge', out2


@test_case
def test_combine_low_intent_confidence_no_rescue():
    rule = _rule(valid=False, reason='没有识别到喷洒类指令')
    edge = {'success': True, 'answers': _answers(intent='spray', ip=0.5, group='g1', gp=0.9)}
    out = ee.combine_command(rule, edge, '随便说点什么', max_duration=5, block_words=[])
    assert not out['valid'] and out['engine'] == 'rule', out


@test_case
def test_combine_edge_unavailable_returns_rule():
    rule = _rule(valid=False, reason='没有识别到喷洒类指令')
    out = ee.combine_command(rule, {'success': False, 'available': False, 'error': 'x'},
                             '随便', max_duration=5)
    assert out['engine'] == 'rule' and out['intent'] == 'unknown', out


# ==================== 状态与降级 ====================
@test_case
def test_status_unavailable_reports_hint_not_silent():
    """模型目录不存在时必须如实报不可用，并给出构建指引。"""
    ee._CFG_CACHE = {'enabled': True, 'mode': 'local',
                     'model_dir': './edge_models/__not_deployed__'}
    try:
        st = ee.status(refresh=True)
        assert st['available'] is False and st['mode'] == 'none', st
        assert 'build_edge_model' in st['hint'], st
        assert st['last_error'], st
    finally:
        ee.reset()
        ee._CFG_CACHE = None


@test_case
def test_detection_state_text():
    txt = ee._state_text_of_detections(
        [{'chinese_name': '蚜虫', 'confidence': 0.876}], {'level': '高风险'})
    assert '蚜虫' in txt and '0.88' in txt and '高风险' in txt, txt
    assert '未检测到' in ee._state_text_of_detections([], {'level': '无风险'})


# ==================== 真模型冒烟（无模型时 SKIP） ====================
@test_case
def test_live_engine_official_routing_example():
    """跑官方 README 的旗舰示例（真模型）：dept 路由应命中 billing。"""
    requires('onnxruntime')
    model = os.path.join(_ROOT, 'edge_models', 'jev-int8')
    if not os.path.exists(os.path.join(model, 'model.onnx')):
        raise SkipTest('未部署 edge_models/jev-int8（构建：python scripts/build_edge_model.py）')
    ee._CFG_CACHE = {'enabled': True, 'mode': 'local', 'model_dir': './edge_models/jev-int8',
                     'timeout': 10.0}
    try:
        r = ee.decide('我的信用卡被扣了两次款，麻烦退一笔。', {
            'dept': {'type': 'choice', 'instructions': '该转给哪个组？',
                     'criteria': {'billing': '支付、扣款、发票、退款',
                                  'technical': '程序缺陷、报错', 'sales': '售前咨询、定价'}},
            'urgent': {'type': 'noul', 'instructions': '这条消息表达了紧急或时间压力'},
        })
        assert r['success'], r
        assert r['answers']['dept']['choice'] == 'billing', r['answers']
        assert r['latency_ms'] is not None
    finally:
        ee.reset()
        ee._CFG_CACHE = None
