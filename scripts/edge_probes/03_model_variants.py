# -*- coding: utf-8 -*-
"""探针6：四个模型变体对照（int8 / fp32 / mixed / kev），单题 vs 合批。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from edgejev import Agent  # noqa: E402
import app.edge_engine as ee  # noqa: E402

QS = ee.COMMAND_QUESTIONS
MODELS = {
    'int8 (335MB)': 'edge_models/jev-int8',
    'fp32 (1.3GB)': 'edge_models/jev-fp32',
    'mixed (369MB)': 'edge_models/jev-mixed',
}
if os.path.exists('edge_models/kev-fp32/model.onnx'):
    MODELS['kev-fp32 (4GB)'] = 'edge_models/kev-fp32'

CASES = [
    ('给片区A喷3秒', 'spray', 'g1', False),
    ('B区再来两秒吧，刚才不够', 'spray', 'g2', False),
    ('把A区的水阀开一小会儿', 'spray', 'g1', False),
    ('给2号片区喷5秒', 'spray', 'g2', False),
    ('给所有片区都喷一下，两秒就够', 'spray', 'all', False),
    ('开启喷洒', 'spray', 'none', False),
    ('停止所有喷洒', 'stop', 'all', False),
    ('A区的雾化关掉', 'stop', 'g1', False),
    ('先别喷了', 'stop', 'none', False),
    ('今天天气怎么样', 'other', 'none', False),
    ('蚜虫怎么防治', 'other', 'none', False),
    ('一直喷不要停', 'spray', 'none', True),
    ('加大剂量喷洒', 'spray', 'none', True),
]

for name, path in MODELS.items():
    try:
        ag = Agent(os.path.join(ROOT, path))
    except Exception as e:
        print('%s: 加载失败 %s' % (name, str(e)[:120]))
        continue
    # 单题模式
    ok_i = ok_g = 0
    for text, wi, wg, _u in CASES:
        r1 = ag.system_one(text, {'intent': QS['intent']})['answers']['intent']
        r2 = ag.system_one(text, {'group': QS['group']})['answers']['group']
        ok_i += (r1['choice'] == wi)
        ok_g += (r2['choice'] == wg)
    # 合批模式（线上口径）
    ok_i4 = ok_g4 = 0
    for text, wi, wg, _u in CASES:
        ans = ag.system_one(text, QS)['answers']
        ok_i4 += (ans['intent']['choice'] == wi)
        ok_g4 += (ans['group']['choice'] == wg)
    print('%-16s | 单题: intent %2d/%d group %2d/%d | 合批: intent %2d/%d group %2d/%d' % (
        name, ok_i, len(CASES), ok_g, len(CASES), ok_i4, len(CASES), ok_g4, len(CASES)))

# 危险判断（合批 unsafe 的分离度）
print('\nunsafe noul（合批，期望 危险>0.75 / 正常<0.6）:')
for name, path in MODELS.items():
    if not os.path.exists(os.path.join(ROOT, path, 'model.onnx')):
        continue
    ag = Agent(os.path.join(ROOT, path))
    vals = []
    for text, _w, _g, want in CASES:
        n = ag.system_one(text, QS)['answers']['unsafe']['noul']
        vals.append('%s%s=%.2f' % ('' if (n >= 0.75) == want else 'x', text[:4], n))
    print(' ', name, ' '.join(vals))
