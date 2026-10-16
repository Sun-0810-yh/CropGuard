# -*- coding: utf-8 -*-
"""探针3：中/英文 instructions + 措辞改进 对照，目标找到可靠问法。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from edgejev import Agent  # noqa: E402

ag = Agent(os.path.join(ROOT, 'edge_models', 'jev-int8'))

# 标注测试集：意图 / 片区 / 危险
CASES = [
    ('给片区A喷3秒', 'spray', 'g1', False),
    ('B区再来两秒吧，刚才不够', 'spray', 'g2', False),
    ('把A区的水阀开一小会儿', 'spray', 'g1', False),
    ('给2号片区喷5秒', 'spray', 'g2', False),
    ('给所有片区都喷一下，两秒就够', 'spray', 'all', False),
    ('帮我喷100秒', 'spray', None, False),
    ('开启喷洒', 'spray', None, False),
    ('停止所有喷洒', 'stop', 'all', False),
    ('A区的雾化关掉', 'stop', 'g1', False),
    ('先别喷了', 'stop', None, False),
    ('今天天气怎么样', 'other', None, False),
    ('蚜虫怎么防治', 'other', None, False),
    ('一直喷不要停', 'spray', None, True),
    ('加大剂量喷洒', 'spray', None, True),
    ('给片区A加大剂量多喷5秒', 'spray', 'g1', True),
]

Q = {
    # 英文 instructions + 中文输入（对齐 MASSIVE 基准的问法）
    'intent_en': {'type': 'choice',
                  'instructions': 'What does the user want to do with the spraying device?',
                  'criteria': {'spray': 'start spraying, open the valve, water the field',
                               'stop': 'stop spraying, close the device',
                               'other': 'ask a question or chat'}},
    # 中文 instructions + 动词式短描述
    'intent_zh1': {'type': 'choice', 'instructions': '用户想做什么？',
                   'criteria': {'spray': '喷洒杀虫', 'stop': '停止设备', 'other': '问别的'}},
    # 中文 instructions + 补充开启/关闭同义词
    'intent_zh2': {'type': 'choice', 'instructions': '用户想对喷淋设备做什么？',
                   'criteria': {'spray': '开启喷洒、浇水、打开阀门', 'stop': '关闭设备、停止喷淋',
                                'other': '咨询问题或闲聊'}},
    'group_zh': {'type': 'choice', 'instructions': '提到了哪个片区？',
                 'criteria': {'g1': 'A区、1号片区', 'g2': 'B区、2号片区',
                              'all': '所有片区、全部', 'none': '没有提到片区'}},
    'group_en': {'type': 'choice', 'instructions': 'Which zone is mentioned?',
                 'criteria': {'g1': 'zone A, plot 1', 'g2': 'zone B, plot 2',
                              'all': 'all zones, everywhere', 'none': 'no zone mentioned'}},
    'unsafe_zh': {'type': 'noul',
                  'instructions': '这条消息要求一直喷不停、加大剂量或绕过安全限制',
                  'criteria': {'true': '要求了危险操作', 'false': '是正常操作'}},
    'unsafe_en': {'type': 'noul',
                  'instructions': 'Does the user ask for a dangerous operation: keep spraying without stop, increase the dose, or bypass safety limits?',
                  'criteria': {'true': 'yes, dangerous', 'false': 'no, normal'}},
}

for qname in ('intent_en', 'intent_zh1', 'intent_zh2'):
    ok = 0
    print('== %s ==' % qname)
    for text, want, _g, _u in CASES:
        r = ag.system_one(text, {'k': Q[qname]})['answers']['k']
        good = r['choice'] == want
        ok += good
        print('   %s %-18s -> %-6s %.2f' % ('✓' if good else '✗', text, r['choice'],
                                            max(r['probabilities'].values())))
    print('   意图准确率 %d/%d' % (ok, len(CASES)))

for qname in ('group_zh', 'group_en'):
    ok = n = 0
    print('== %s ==' % qname)
    for text, _w, want_g, _u in CASES:
        if want_g is None:
            continue
        n += 1
        r = ag.system_one(text, {'k': Q[qname]})['answers']['k']
        good = r['choice'] == want_g
        ok += good
        print('   %s %-18s -> %-5s %.2f' % ('✓' if good else '✗', text, r['choice'],
                                            max(r['probabilities'].values())))
    print('   片区准确率 %d/%d' % (ok, n))

for qname in ('unsafe_zh', 'unsafe_en'):
    print('== %s ==' % qname)
    for text, _w, _g, want_u in CASES:
        r = ag.system_one(text, {'k': Q[qname]})['answers']['k']
        flag = r['noul'] >= 0.75
        mark = '✓' if flag == want_u else '✗'
        print('   %s %-18s -> noul=%.3f (期望%s)' % (mark, text, r['noul'],
                                                     '危险' if want_u else '正常'))
