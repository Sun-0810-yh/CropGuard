# -*- coding: utf-8 -*-
"""本地大模型问答网关（RAG + 防幻觉），GUI / 网页 / 手机端唯一的 AI 出入口。

## 数据流

    用户问题 (+ 当前检测结果 / 历史对话)
        ↓  llm.retriever 召回（TF-IDF 字符 n-gram，见 retriever.py）
    知识片段列表（带 id / 标题 / 分数 / 来源）
        ↓  召回闸门：最高分 < CHAT.refuse_below ⇒ 直接拒答（不调用大模型！）
    接地提示词（prompts/templates/grounded_expert.txt）
        ↓  llm.local_llm → 本机 Ollama qwen2.5:7b（temperature=0.2、固定 seed）
    模型回答
        ↓  llm.guard 后校验：编造药名/剂量 → 打标；覆盖率过低 → 判为无依据
    最终回答 + 引用条目 + guard 明细（写入 output/qa_history.jsonl 留痕）

## 关键设计

* **无依据不生成**：低于阈值时连模型都不调，直接返回统一拒答话术，
  这样"编造"在流程上就不可能发生（而不是靠提示词祈祷）。
* **不复用 1500 字报告提示词**：旧提示词要求长报告，本地 7B 必然靠编造凑字，
  已由接地模板取代（见 `prompts/templates/grounded_expert.txt`）。
* **同一入口**：GUI 聊天、`POST /api/ask`、手机页都走 `answer()`，
  三处行为一致，不会出现"网页老实、GUI 乱答"的分裂。
* **不驱动硬件**：大模型输出永远只是文本；喷淋必须走
  `POST /api/ask_command` 的意图白名单 + 人工确认 + `/api/spray`。
"""
import json
import os
import re
import threading
import time
from datetime import datetime

from llm import guard, local_llm
from llm.retriever import get_retriever as _get_retriever

# 保持旧调用点兼容：api_server.py 直接取召回器单例
get_retriever = _get_retriever

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROMPT_PATH = os.path.join(ROOT, 'prompts', 'templates', 'grounded_expert.txt')

_DEFAULT_CFG = {
    'max_history_turns': 6,
    'refuse_below': 0.18,
    'timeout': 120,
    'log_path': './output/qa_history.jsonl',
    'seed': 20261008,
    'top_k': 4,
}

_log_lock = threading.Lock()
_prompt_cache = None


# ---------------------------------------------------------------- 配置
def load_cfg(config=None):
    """从 configs.yaml 读 CHAT/RAG 配置（缺省值保证老配置也能跑）。"""
    cfg = dict(_DEFAULT_CFG)
    if config is None:
        try:
            from tool.parser import get_config
            config = get_config(os.path.join(ROOT, 'config', 'configs.yaml'))
        except Exception:
            config = {}
    chat = (config.get('CHAT', {}) or {}) if config else {}
    rag = (config.get('RAG', {}) or {}) if config else {}
    for k in ('max_history_turns', 'refuse_below', 'timeout', 'log_path', 'seed', 'enabled'):
        if k in chat:
            cfg[k] = chat[k]
    if 'top_k' in rag:
        cfg['top_k'] = rag['top_k']
    if 'retriever' in rag:
        cfg['retriever'] = rag['retriever']
    return cfg


# ---------------------------------------------------------------- 提示词
def _system_prompt():
    global _prompt_cache
    if _prompt_cache is None:
        try:
            with open(_PROMPT_PATH, 'r', encoding='utf-8') as f:
                _prompt_cache = f.read().strip()
        except Exception:
            _prompt_cache = (
                '你是农业植保助手。只能依据给定资料回答；资料不足就回答'
                '"知识库未涵盖"，禁止编造农药名称与剂量。回答不超过300字，'
                '结尾列出依据片段标题。')
    return _prompt_cache


def _format_context(chunks):
    lines = []
    for i, c in enumerate(chunks, 1):
        lines.append(f"[片段{i}] 标题：{c['title']}（相似度 {c.get('score', 0):.2f}，"
                     f"来源：{c.get('source', '')}）\n{c['text']}")
    return '\n\n'.join(lines)


def _format_history(history, max_turns):
    """把历史对话压成文本（只取最近 N 轮，避免提示词过长拖慢 7B）。"""
    if not history:
        return '（无）'
    msgs = [m for m in history if isinstance(m, dict) and m.get('content')]
    msgs = msgs[-2 * int(max_turns or 0):] if max_turns else []
    if not msgs:
        return '（无）'
    out = []
    for m in msgs:
        role = '用户' if m.get('role') == 'user' else '助手'
        out.append(f'{role}：{str(m.get("content"))[:300]}')
    return '\n'.join(out)


# ---------------------------------------------------------------- 上下文
def pests_from_context(context):
    """从"当前检测结果"里抽出害虫英文类名（retriever 的 pest 键）。"""
    if not context:
        return []
    dets = context.get('detections') or []
    keys = []
    for d in dets:
        if not isinstance(d, dict):
            continue
        k = d.get('class_name') or d.get('pest') or ''
        if k and k not in keys:
            keys.append(str(k))
    return keys


def context_summary(context):
    """把检测结果压成一句事实描述（只陈述检测器输出，不添加解释）。"""
    if not context:
        return ''
    dets = context.get('detections') or []
    risk = (context.get('risk') or {}).get('level', '')
    if not dets and not risk:
        return ''
    parts = []
    if dets:
        items = [f"{d.get('chinese_name') or d.get('class_name')}"
                 f"（置信度{float(d.get('confidence') or 0):.2f}）" for d in dets[:8]]
        parts.append('本次识别结果：' + '、'.join(items))
    if risk:
        parts.append(f'风险等级：{risk}；{ (context.get("risk") or {}).get("detail", "") }')
    return '\n'.join(parts)


# ---------------------------------------------------------------- 留痕
def _log_qa(record, path):
    if not path:
        return
    try:
        full = path if os.path.isabs(path) else os.path.join(ROOT, path.lstrip('./'))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with _log_lock:
            with open(full, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception:
        pass


# ---------------------------------------------------------------- RAG 阶段
def _retrieve(question, context, cfg, retriever=None):
    """召回 + 格式化引用。返回 (chunks, top_score, citations)。"""
    r = retriever or get_retriever()
    ctx_pests = pests_from_context(context)
    named = r.detect_pests(question)
    chunks = r.search(question, top_k=int(cfg.get('top_k', 4)),
                      pest_filter=named or None,
                      context_pests=ctx_pests or None)
    top = chunks[0]['score'] if chunks else 0.0
    citations = [{'id': c['id'], 'title': c['title'], 'type': c['type'],
                  'pest': c['pest'], 'cn': c['cn'], 'score': c['score'],
                  'source': c.get('source', '')} for c in chunks]
    return chunks, top, citations


def _gate_response(chunks, top, cfg, t0):
    """召回闸门：没依据就不生成。命中闸门返回拒答 dict，否则返回 None。"""
    refuse_below = float(cfg.get('refuse_below', 0.18))
    if not chunks or top < refuse_below:
        return {
            'success': True, 'source': 'refused', 'answer': guard.REFUSAL_TEMPLATE,
            'citations': [], 'engine': 'none', 'elapsed': round(time.time() - t0, 3),
            'guard': {'ok': True, 'reason': 'below_threshold',
                      'top_score': round(top, 4), 'threshold': refuse_below},
            'chunks': [],
        }
    return None


def _build_prompt(question, history, context, chunks, cfg):
    """拼接地提示词。返回 (system_prompt, user_prompt, context_text)。"""
    context_text = _format_context(chunks)
    enriched_question = question
    summary = context_summary(context)
    if summary:
        enriched_question = f'{summary}\n\n用户问题：{question}'

    system_prompt = _system_prompt()
    user_prompt = (system_prompt
                   .replace('{context}', context_text)
                   .replace('{history}', _format_history(history, cfg.get('max_history_turns')))
                   .replace('{question}', enriched_question))
    return system_prompt, user_prompt, context_text


def _generate(user_prompt, cfg):
    """调本地大模型。返回 (text, engine, error)。"""
    try:
        text = local_llm.generate(
            '你是农智云系统的农业植保问答助手，回答必须严格依据用户消息中的【知识片段】。',
            user_prompt,
            timeout=float(cfg.get('timeout', 120)),
            temperature=0.2,
            num_predict=400,
            seed=cfg.get('seed'),
            top_p=0.8,
        )
        return text, local_llm.MODEL_NAME, ''
    except Exception as exc:
        return '', 'none', f'{type(exc).__name__}: {str(exc)[:160]}'


def _kb_fallback(chunks, citations, engine_err, question, cfg, t0):
    """大模型不可用：降级为知识库原文拼接。"""
    kb_text = '\n\n'.join(f'{c["title"]}：{c["text"]}' for c in chunks[:2])
    rec = {
        'ts': datetime.now().isoformat(timespec='seconds'), 'question': question,
        'source': 'knowledge_base', 'top_score': round(citations[0]['score'], 4) if citations else 0.0,
        'citations': citations, 'answer_len': len(kb_text),
        'guard': {'reason': 'llm_unavailable', 'error': engine_err},
    }
    _log_qa(rec, cfg.get('log_path'))
    return {
        'success': True, 'source': 'knowledge_base',
        'answer': guard.with_safety_tail(kb_text, guard.danger_notes(question)),
        'citations': citations, 'engine': 'none',
        'elapsed': round(time.time() - t0, 3),
        'guard': {'ok': True, 'reason': 'llm_unavailable', 'error': engine_err},
        'chunks': chunks,
    }


def _guard_and_finalize(text, context_text, question):
    """后校验：编造药名/剂量就打标并替换。返回 (clean_text, guard_result)。"""
    check = guard.check_answer(text, context_text)
    clean = text
    if check['unsupported'] or check['dosage_unsupported']:
        clean = guard.strip_unsupported(text, check['unsupported'],
                                        check['dosage_unsupported'])
    if not check['ok']:
        clean = (clean + '\n\n（系统校验：本次回答中有内容未能在知识库片段中找到依据，'
                          '已标注，请以当地植保部门指导为准。）')
    clean = guard.with_safety_tail(clean, guard.danger_notes(question, text))
    return clean, check


def _finalize_llm(question, clean, engine, citations, chunks, check, cfg, t0):
    """组装 LLM 成功/后校验后的返回。"""
    rec = {
        'ts': datetime.now().isoformat(timespec='seconds'), 'question': question,
        'source': 'llm', 'engine': engine, 'top_score': round(citations[0]['score'], 4) if citations else 0.0,
        'citations': citations, 'answer_len': len(clean), 'guard': check,
    }
    _log_qa(rec, cfg.get('log_path'))
    return {
        'success': True, 'source': 'llm', 'answer': clean, 'citations': citations,
        'engine': engine, 'elapsed': round(time.time() - t0, 3), 'guard': check,
        'chunks': chunks,
    }


# ---------------------------------------------------------------- 主入口
def answer(question, history=None, context=None, cfg=None, retriever=None):
    """回答用户提问（唯一 AI 入口）。

    question : 用户问题
    history  : [{'role': 'user'|'assistant', 'content': str}, ...]
    context  : {'detections': [...], 'risk': {...}}（可选，一般是当前检测结果）
    cfg      : 覆盖配置（测试用）
    返回 dict：success / answer / source(llm|refused|knowledge_base) /
              citations / engine / elapsed / guard / chunks
    """
    t0 = time.time()
    cfg = dict(_DEFAULT_CFG, **(cfg or load_cfg()))
    question = str(question or '').strip()
    if not question:
        return {'success': False, 'error': '问题为空', 'source': 'none', 'answer': '',
                'citations': [], 'guard': {}, 'elapsed': 0.0}

    chunks, top, citations = _retrieve(question, context, cfg, retriever=retriever)

    # ---- 召回闸门：没依据就不生成 ----
    gated = _gate_response(chunks, top, cfg, t0)
    if gated:
        rec = {
            'ts': datetime.now().isoformat(timespec='seconds'),
            'question': question, 'source': 'refused', 'top_score': round(top, 4),
            'citations': [], 'answer_len': 0, 'guard': {'reason': 'below_threshold'},
        }
        _log_qa(rec, cfg.get('log_path'))
        return gated

    _system_prompt_used, user_prompt, context_text = _build_prompt(
        question, history, context, chunks, cfg)
    text, engine, engine_err = _generate(user_prompt, cfg)

    if not str(text).strip():
        return _kb_fallback(chunks, citations, engine_err, question, cfg, t0)

    clean, check = _guard_and_finalize(text, context_text, question)
    return _finalize_llm(question, clean, engine, citations, chunks, check, cfg, t0)


# ---------------------------------------------------------------- 指令意图（不执行！）
# 注意：不要用「单个拉丁字母 a/b」做子串匹配 —— "帮我喷100秒" 里的 "帮" 是拼音
# 里的 pāng，但 utf-8 通配不会撞上；真正的坑是英文串里出现 a/b 导致误判，
# 所以这里字母一律用正则词边界匹配，中文词才用子串匹配。
_GROUP_WORDS = {
    'g1': [r'(?<![a-z0-9])a(?![a-z0-9])', 'a区', 'a片区', '1号', '片区a', '一区', '一带',
           '片区1', '第1片区', '1片区', '一区'],
    'g2': [r'(?<![a-z0-9])b(?![a-z0-9])', 'b区', 'b片区', '2号', '片区b', '二区', '二带',
           '片区2', '第2片区', '2片区', '二区'],
    'all': ['all', '全部', '全体', '都', '所有', '全部片区'],
}
_SPRAY_WORDS = ['喷', '喷洒', '喷淋', '喷药', '打药', '洒水', '雾化', '执行', '启动']

# 停止类关键词（2026-10-16 扩充）：
# 实测漏判 "A区的雾化关掉"/"把泵关了吧"/"给B区关一下" —— 原来只认 '停止/停下/关闭喷洒/别喷'，
# "关掉/关了/关一下" 这类口语全部漏掉，会被后面的喷洒词（雾化/喷）误判成"开始喷洒"。
# ⚠️ 危险词检查必须先于本表执行（见 parse_command），否则 "关掉安全限制" 会被当成停止指令。
_STOP_WORDS = ('停止', '停下', '关闭', '关掉', '关了', '关一下', '取消喷洒', '结束喷洒',
               '别喷', '不要喷')

# 危险词：出现这些一律不允许直接执行，只给人工确认/拒绝
# 注意要覆盖"加大剂量/多喷/超量"这类会伤作物或烧雾化片的说法，
# 以及"关闭/绕过安全限制"这类绕过安全边界的说法
_BLOCK_WORDS = ['一直喷', '持续喷', '不停', '循环喷', '最大', '全开', '关掉安全',
                '关闭安全', '绕过安全', '解除安全', '加大剂量', '加量', '多喷', '超量',
                '不限时', '无限']


def _match_group(text):
    """识别提问里的片区：g1 / g2 / all / None。"""
    for g, words in _GROUP_WORDS.items():
        for w in words:
            if w.startswith('(?') or w.startswith('['):
                if re.search(w, text):
                    return g
            elif w in text:
                return g
    return None


_CN_NUM = {'零': 0, '一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5,
           '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}


def _parse_duration(text, default=3, max_duration=5):
    """从文本里取秒数（支持"3秒/三秒/两秒/10s"），并按安全上限钳制。"""
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:秒|s\b|second)', text)
    if not m:
        m = re.search(r'([零一两二三四五六七八九十])\s*(?:秒|s\b)', text)
        if m:
            val = _CN_NUM.get(m.group(1), default)
            return max(1, min(int(val), int(max_duration))), int(val)
    if not m:
        m = re.search(r'(\d+(?:\.\d+)?)', text)
    if not m:
        return int(default), int(default)
    try:
        val = int(float(m.group(1)))
    except Exception:
        val = int(default)
    return max(1, min(val, int(max_duration))), val


def parse_command(text, max_duration=5):
    """把自然语言解析成"待确认"的喷洒意图（绝不直接下发）。

    返回 {'intent', 'group', 'duration', 'valid', 'reason', 'needs_confirm', 'reply'}
    """
    t = re.sub(r'\s+', '', str(text or '').lower())
    raw = str(text or '')
    result = {'intent': 'unknown', 'group': None, 'duration': None, 'valid': False,
              'needs_confirm': True, 'max_duration': int(max_duration),
              'reason': '', 'reply': ''}

    # ⚠️ 顺序很重要：危险词检查放在最前面 —— 它是安全底线，任何情况下都不放行。
    #    （反例：不加这一层时 "关闭安全限制" 会被"关闭"匹配成停止指令。）
    for kw in _BLOCK_WORDS:
        if kw in t:
            result.update(intent='spray', valid=False,
                          reason=f'含不安全表述"{kw}"',
                          reply='该指令不符合安全规则（雾化片单次喷淋有硬上限），'
                                '已拒绝。需要请手动点选片区分组与时长。')
            return result

    if any(k in t for k in _STOP_WORDS):
        # 停止也尽量读到片区：说"A区关掉"只停 A 区，不误停全体
        stop_group = _match_group(t) or 'all'
        result.update(intent='stop', group=stop_group, duration=0, valid=True,
                      reply='已解析为"停止喷洒"意图，请确认后执行。')
        return result

    if not any(w in t for w in _SPRAY_WORDS):
        result.update(intent='unknown',
                      reason='没有识别到喷洒类指令',
                      reply='我没有识别到喷洒指令。可以说"给片区A喷3秒"。')
        return result

    result['intent'] = 'spray'

    group = _match_group(t)
    if group is None:
        result.update(valid=False, reason='未指定片区',
                      reply='请指定片区：A 片区(g1)、B 片区(g2) 或全体喷洒(all)。')
        return result

    capped, duration = _parse_duration(t, default=3, max_duration=max_duration)
    note = ''
    if capped != duration:
        note = f'（请求 {duration} 秒已按安全上限钳制为 {capped} 秒）'

    result.update(group=group, duration=capped, valid=True,
                  reason='', reply=f'解析为：给 {group} 喷洒 {capped} 秒{note}。'
                                   f'请确认后执行。')
    return result
