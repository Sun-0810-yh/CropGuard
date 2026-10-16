# -*- coding: utf-8 -*-
"""探针8：停止类意图概率（校准 RESCUE_STOP_MIN）+ 规则解析现状核对。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from edgejev import Agent  # noqa: E402
import app.edge_engine as ee  # noqa: E402
import llm.ai_gateway as gw   # noqa: E402

ag = Agent(os.path.join(ROOT, 'edge_models', 'jev-int8'))
QS = ee.COMMAND_QUESTIONS

texts = ['把A区的水阀开一小会儿', 'A区的雾化关掉', '先别喷了', 'B区再来两秒吧，刚才不够',
         '把泵关了吧', '让B区停下来', '别让它喷了', '把水停了', '给B区关一下',
         '开一下B区的雾化', '把A区的水打开']
print('%-20s | 规则解析 | Edge合批 intent(stop概率) ' % '输入')
print('-' * 78)
for t in texts:
    r = gw.parse_command(t, max_duration=5)
    a = ag.system_one(t, QS)['answers']['intent']
    print('%-20s | %-8s %-5s %s | %s stop=%.3f' % (
        t, r['intent'], r.get('group') or '-', '✓' if r['valid'] else '✗',
        a['choice'], a['probabilities'].get('stop', 0)))
