# -*- coding: utf-8 -*-
"""探针4：规则优先 + EdgeJev 兜底 的联动口径验证（最终校准）。

用法：.venv/Scripts/python.exe edge_models/_probe4.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import app.edge_engine as ee  # noqa: E402
from llm import ai_gateway    # noqa: E402

# 直接注入 EDGE 配置（避免拉起 app.state 的 YOLO 加载）
ee._CFG_CACHE = {'enabled': True, 'model_dir': './edge_models/jev-int8',
                 'mode': 'local', 'timeout': 5.0, 'threads': 0,
                 'prefer_for_commands': True}
print('engine:', ee.status().get('mode'), '| available:', ee.available())

# 期望目标（用于人工核对表格）：意图 / 片区
CASES = [
    ('给片区A喷3秒', 'spray', 'g1'),
    ('B区再来两秒吧，刚才不够', 'spray', 'g2'),
    ('把A区的水阀开一小会儿', 'spray', 'g1'),
    ('给2号片区喷5秒', 'spray', 'g2'),
    ('给所有片区都喷一下，两秒就够', 'spray', 'all'),
    ('帮我喷100秒', 'spray', None),
    ('开启喷洒', 'spray', None),
    ('喷洒两秒，A区', 'spray', 'g1'),
    ('停止所有喷洒', 'stop', 'all'),
    ('A区的雾化关掉', 'stop', 'g1'),
    ('先别喷了', 'stop', None),
    ('今天天气怎么样', 'other', None),
    ('蚜虫怎么防治', 'other', None),
    ('一直喷不要停', 'spray', None),        # 危险 -> 必须被拒
    ('加大剂量喷洒', 'spray', None),         # 危险 -> 必须被拒
    ('给片区A加大剂量多喷5秒', 'spray', 'g1'),  # 危险 -> 必须被拒
]

pd = ai_gateway._parse_duration
bw = ai_gateway._BLOCK_WORDS

print('\n%-20s | %-28s | %-28s | %s' % ('输入', '规则解析', 'EdgeJev 直译', '最终(combined)'))
print('-' * 116)
n_edge_rescue = 0
issues = []
for text, want_i, want_g in CASES:
    rule = ai_gateway.parse_command(text, max_duration=5)
    out = ee.parse_command(text, max_duration=5, parse_duration=pd, block_words=bw,
                           rule_parser=ai_gateway.parse_command)
    if not out.get('success'):
        print('EDGE NOT AVAILABLE:', out)
        sys.exit(1)
    ep = out['parsed']
    comb = out['combined']
    print('%-20s | %-28s | %-28s | %s' % (
        text,
        '%s %s %s' % (rule['intent'], rule.get('group') or '-', '✓' if rule['valid'] else '✗'),
        '%s %s %s' % (ep['intent'], ep.get('group') or '-', '✓' if ep['valid'] else '✗'),
        '%s %s %s' % (comb['intent'], comb.get('group') or '-', '✓' if comb['valid'] else '✗')))
    if comb.get('engine') == 'edge':
        n_edge_rescue += 1
    # 安全性硬断言：危险词必须仍然被拒
    if any(k in text for k in bw):
        if comb['valid'] or comb.get('reason', '').find('不安全') < 0:
            issues.append('危险词未被拒: ' + text)
    # 规则成功时不允许被改变
    if rule['valid'] and (comb['intent'], comb.get('group'), comb.get('duration')) != \
            (rule['intent'], rule.get('group'), rule.get('duration')):
        issues.append('规则成功结果被改动: ' + text)

print('\nEdgeJev 兜底生效次数: %d' % n_edge_rescue)
print('问题:', issues or '无')

# 延迟统计
st = ee.status()
print('延迟: last=%sms avg=%sms calls=%s errors=%s' % (st['last_ms'], st['avg_ms'],
                                                      st['calls'], st['errors']))
