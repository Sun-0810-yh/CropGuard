# -*- coding: utf-8 -*-
"""Web 测评页（/eval）共用逻辑测试：评分口径、样本清单、报告行、页面渲染。

评分函数本身（compute_eval_score 边界）已由 test_eval_scoring.py 钉死；
这里只测"Web 侧新增的那层"——它与 GUI 共用同一份函数，测的是管路有没有接错。
"""
import os
import shutil
import sys

from _registry import test_case

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app import eval_score as es   # noqa: E402

_TMP = os.path.join(_ROOT, 'output', '_test_tmp', 'eval_web')


# ==================== 口径 ====================
@test_case
def test_rubric_defaults_match_field_caliber():
    r = es.rubric_from_config({})
    assert r['target'] == 20 and r['pps'] == 1.0, r
    assert r['limit'] == 180.0 and r['time_full'] == 10.0 and r['penalty'] == 0.5, r
    assert r['acc_full'] == 20.0 and r['total_full'] == 30.0, r


@test_case
def test_score_with_rubric_consistent_with_gui_functions():
    """Web 侧算分必须与 GUI 口径逐值一致（评委会对数字）。"""
    recs = [{'verdict': '正确'}] * 3 + [{'verdict': '错误'}] + [{'verdict': None}]
    r = es.rubric_from_config({})
    s = es.score_with_rubric(recs, 185.0, r)
    expect = es.compute_eval_score(recs, 185.0, target=20, pps=1.0, limit=180.0,
                                   time_full=10.0, penalty=0.5)
    assert s == expect, (s, expect)
    assert s['acc'] == 3.0, s
    assert abs(s['time_score'] - 7.5) < 1e-6, s      # 10 - 5×0.5
    assert abs(s['score'] - 10.5) < 1e-6, s


# ==================== 样本清单 ====================
@test_case
def test_list_sample_images_filters_sorts_and_handles_missing():
    assert es.list_sample_images('') == []
    assert es.list_sample_images(os.path.join(_TMP, '__nope__')) == []
    shutil.rmtree(_TMP, ignore_errors=True)
    os.makedirs(_TMP, exist_ok=True)
    try:
        for name in ('b.JPG', 'a.jpg', 'c.png', 'note.txt', '.hidden'):
            with open(os.path.join(_TMP, name), 'wb') as f:
                f.write(b'x')
        os.makedirs(os.path.join(_TMP, 'sub'), exist_ok=True)
        got = [s['name'] for s in es.list_sample_images(_TMP)]
        assert got == ['a.jpg', 'b.JPG', 'c.png'], got     # 排序 + 只留图片 + 过滤目录
        assert es.list_sample_images(_TMP, limit=2) and len(es.list_sample_images(_TMP, limit=2)) == 2
        assert es.list_sample_images(_TMP)[0]['size'] == 1
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)


@test_case
def test_is_image_name():
    assert es.is_image_name('x.JPG') and es.is_image_name('y.jpeg') and es.is_image_name('z.png')
    assert not es.is_image_name('x.txt') and not es.is_image_name('noext') and not es.is_image_name('')


# ==================== 报告行（与 GUI csv 对齐） ====================
@test_case
def test_build_report_rows_layout_and_summary():
    records = [
        {'no': 1, 'name': 'a.jpg', 'time': '10:00:01',
         'detections': [{'chinese_name': '蚜虫', 'confidence': 0.876}],
         'consum': 0.12, 'elapsed': 0.3, 'verdict': '正确'},
        {'no': 2, 'name': 'b.jpg', 'time': '10:00:02',
         'detections': [], 'consum': 0.11, 'elapsed': 0.6},
    ]
    rows, score = es.build_report_rows(records, 0.6, es.rubric_from_config({}))
    assert rows[0] == ['编号', '图片路径', '输入时间', '识别结果(中文)', '目标数量',
                       '推理耗时', '本轮用时(s)', '评判'], rows[0]
    assert rows[1][3] == '蚜虫(0.88)', rows[1]
    assert rows[2][3] == '无目标' and rows[2][7] == '未评判', rows[2]
    flat = ['|'.join(str(c) for c in row) for row in rows]
    assert any('现场评测汇总' in x for x in flat), flat
    assert any(x.startswith('总分') and '30' in x for x in flat), flat
    assert score['correct'] == 1 and score['judged'] == 1


@test_case
def test_build_report_rows_fmt_keeps_integers_clean():
    assert es._fmt_num(20.0) == '20'
    assert es._fmt_num(9.5) == '9.5'
    assert es._fmt_num('5') == '5'


# ==================== 页面渲染 ====================
@test_case
def test_eval_page_renders_with_placeholders_filled():
    from serve import eval_page
    html = eval_page.render(config={})
    assert '{{STYLE}}' not in html and '{{SCRIPT}}' not in html, '占位符必须被替换'
    assert '现场测评' in html and '开始测评' in html, '页面骨架缺失'
    assert 'renderScore' in html, '脚本没被内联进去'


@test_case
def test_edge_page_and_eval_nav_exist():
    from serve import pages as static_pages
    html = static_pages.load('edge')
    assert '推理入口' in html and 'systemone' in html, 'EdgeJev 页面缺失关键内容'
    idx = static_pages.load('index')
    assert '/eval' in idx and '/edge' in idx, '首页应给两个新页面加入口'
