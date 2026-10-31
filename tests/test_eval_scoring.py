# -*- coding: utf-8 -*-
"""现场评分口径的纯逻辑用例（不建窗口、不碰硬件）。

口径（2026-10-11 与用户对齐）：
    准确度 20 分 = 正确样本数 × points_per_sample（20 × 1）
    时效性 10 分 = 拍满 20 张才有：10 − max(0, 用时 − 180) × 0.5，扣完为止；没拍满得 0
    总分   = 准确度 + 时效性（满分 30）

这些数字会被评委现场核对，所以把边界全钉死。
"""
import importlib.util
import os

from _registry import test_case

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MOD = None


def _mod():
    """惰性加载 main.py —— 只为拿 compute_eval_score，不建窗口。

    注意：main.py 顶部依赖 ultralytics / PyQt5；缺失时直接 SKIP 而不是失败。
    """
    global _MOD
    if _MOD is not None:
        return _MOD
    from _registry import requires
    requires('ultralytics', 'PyQt5')
    spec = importlib.util.spec_from_file_location('cg_eval_score',
                                                  os.path.join(_ROOT, 'main.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # __name__ != '__main__'，不会启动 GUI
    _MOD = mod
    return mod


def _recs(n_correct, n_wrong=0, n_unjudged=0):
    out = [{'verdict': '正确'} for _ in range(n_correct)]
    out += [{'verdict': '错误'} for _ in range(n_wrong)]
    out += [{'verdict': None} for _ in range(n_unjudged)]
    return out


# ==================== 时效性 ====================
FULL = [{'verdict': None}] * 20      # 拍满 20 张、还没评判


@test_case
def test_time_score_full_within_limit():
    """≤180s 得满分（边界 180 也算满分）。"""
    m = _mod()
    for elapsed in (0.0, 60.0, 179.9, 180.0):
        s = m.compute_eval_score(FULL, elapsed)
        assert s['time_score'] == 10.0, (elapsed, s)
        assert s['over'] == 0.0, (elapsed, s)


@test_case
def test_time_score_penalty_per_second():
    """每超时 1 秒扣 0.5 分。"""
    m = _mod()
    cases = {180.5: 9.8, 181: 9.5, 182: 9.0, 190: 5.0, 200: 0.0}
    for elapsed, expect in cases.items():
        s = m.compute_eval_score(FULL, elapsed)
        assert abs(s['time_score'] - expect) < 1e-6, (elapsed, s['time_score'], expect)


@test_case
def test_time_score_floors_at_zero():
    """扣完为止：超时再多也不出现负分。"""
    m = _mod()
    for elapsed in (200.0, 210.0, 600.0, 9999.0):
        s = m.compute_eval_score(FULL, elapsed)
        assert s['time_score'] == 0.0, (elapsed, s)
        assert s['score'] >= 0.0, s


@test_case
def test_over_reported():
    """超时秒数要如实报出来（面板上显示 +Ns 超时）。"""
    m = _mod()
    assert m.compute_eval_score([], 190.0)['over'] == 10.0
    assert m.compute_eval_score([], 150.0)['over'] == 0.0


# ==================== 准确度 ====================
@test_case
def test_accuracy_one_point_each():
    """每正确 1 个样本得 1 分，满分 20。"""
    m = _mod()
    s = m.compute_eval_score(_recs(7), 100.0)
    assert s['acc'] == 7.0, s
    assert s['correct'] == 7 and s['judged'] == 7 and s['total'] == 7, s


@test_case
def test_accuracy_full_house():
    """20 个全对：准确度 20 + 时效 10 = 30。"""
    m = _mod()
    s = m.compute_eval_score(_recs(20), 150.0)
    assert s['acc'] == 20.0, s
    assert s['score'] == 30.0, s
    assert s['finished'] is True, s


@test_case
def test_accuracy_capped_at_target():
    """超过目标样本数时准确度封顶，不会算成 21 分。"""
    m = _mod()
    s = m.compute_eval_score(_recs(25), 150.0)
    assert s['acc'] == 20.0, s
    assert s['total'] == 25, '样本数仍如实统计'


@test_case
def test_unjudged_not_counted_as_correct():
    """漏判只少分，绝不能被当成对（未评判 ≠ 正确）。"""
    m = _mod()
    s = m.compute_eval_score(_recs(3, n_unjudged=5), 150.0)
    assert s['correct'] == 3, s
    assert s['judged'] == 3, s
    assert s['total'] == 8, s
    assert s['acc'] == 3.0, s


@test_case
def test_wrong_does_not_add_score():
    """判错的样本不加分（但算已评判）。"""
    m = _mod()
    s = m.compute_eval_score(_recs(2, n_wrong=3), 150.0)
    assert s['acc'] == 2.0, s
    assert s['judged'] == 5, s


# ==================== 组合与边界 ====================
@test_case
def test_zero_samples():
    """一个样本都没有时不该崩，也没有时效分（场景：刚开始就停表）。"""
    m = _mod()
    s = m.compute_eval_score([], 0.0)
    assert s['total'] == 0 and s['acc'] == 0.0, s
    assert s['time_score'] == 0.0 and s['score'] == 0.0, s


@test_case
def test_time_score_needs_all_samples():
    """识别环节拍满 20 张才有时效分：19 张得 0，20 张在 180 秒内得 10。"""
    m = _mod()
    assert m.compute_eval_score(_recs(19), 100.0)['time_score'] == 0.0
    s = m.compute_eval_score(_recs(13, 7), 100.0)
    assert s['time_score'] == 10.0 and s['acc'] == 13.0 and s['score'] == 23.0, s


@test_case
def test_wrong_does_not_change_score():
    """正确一张 1 分；错误和未评判都不加也不减。"""
    m = _mod()
    a = m.compute_eval_score(_recs(5, 0, 15), 100.0)
    b = m.compute_eval_score(_recs(5, 15), 100.0)
    assert a['score'] == b['score'] == 15.0, (a, b)


@test_case
def test_worst_case_is_zero():
    """全错 + 严重超时 = 0 分（不出现负分）。"""
    m = _mod()
    s = m.compute_eval_score(_recs(0, n_wrong=20), 400.0)
    assert s['score'] == 0.0, s


@test_case
def test_score_is_rounded_to_one_decimal():
    """得分保留 1 位小数（面板与报告展示口径一致）。"""
    m = _mod()
    s = m.compute_eval_score(_recs(3, 0, 17), 181.0)     # 3 + 9.5 = 12.5
    assert s['score'] == 12.5, s
    s2 = m.compute_eval_score(_recs(3, 0, 17), 185.0)    # 3 + 7.5 = 10.5
    assert s2['score'] == 10.5, s2


@test_case
def test_config_defaults_match_rubric():
    """配置默认值必须与现场口径一致（防被误改）。"""
    m = _mod()
    assert m.EVAL_TARGET == 20, m.EVAL_TARGET
    assert m.EVAL_POINTS_PER_SAMPLE == 1.0, m.EVAL_POINTS_PER_SAMPLE
    assert m.EVAL_TIME_LIMIT == 180.0, m.EVAL_TIME_LIMIT
    assert m.EVAL_TIME_FULL == 10.0, m.EVAL_TIME_FULL
    assert m.EVAL_OVERTIME_PENALTY == 0.5, m.EVAL_OVERTIME_PENALTY
    assert m.EVAL_ACC_FULL == 20.0, m.EVAL_ACC_FULL
    assert m.EVAL_TOTAL_FULL == 30.0, m.EVAL_TOTAL_FULL


@test_case
def test_score_accepts_overrides():
    """口径可改名（万一规则再变）：参数覆盖要生效。"""
    m = _mod()
    s = m.compute_eval_score(_recs(10), 100.0, target=10, pps=2.0,
                             limit=60, time_full=5.0, penalty=1.0)
    assert s['acc'] == 20.0, s          # 10 × 2.0
    assert s['time_score'] == 0.0, s    # 5 − (100−60)×1.0 < 0 → 0
    assert s['score'] == 20.0, s
