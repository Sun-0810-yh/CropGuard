# -*- coding: utf-8 -*-
"""EdgeJev 本地类型化决策引擎（部署适配层）。

## 它解决什么问题

CropGuard 的一些"理解类"环节（一句话 -> 结构化决策）此前依赖规则正则或本地大模型：
规则正则覆盖不了改写，大模型（qwen2.5:7b）单次要几秒。EdgeJev 把这类**类型化决策**
放到本机 CPU 上完成：单题 15ms 量级、全程离线、不需要 API key、不装 torch。

## 部署形态（status() 里按顺序自动探测）

1. ``local`` —— 进程内直调：仓库内自带 ``CropGuard/edgejev/`` 运行包
   + onnxruntime/tokenizers + ``edge_models/jev-int8``（`scripts/build_edge_model.py` 产出）。
2. ``http``  —— 连本地服务：``edgejev serve --model ./edge_models/jev-int8 --port 8009``
   的官方协议 ``POST /v1/systemone``（可独立进程 / 独立 venv 部署）。
3. ``none``  —— 什么都没部署：如实报告不可用，接口返回 ``available=false``，
   并给出构建指引；**绝不静默假装**。

## 安全边界（与 CropGuard 既有口径一致，不可绕过）

* EdgeJev 只做"理解"（意图/片区/时长/危险度），**绝不直接下发硬件**：
  解析结果必须回到 ``/api/ask_command`` 的既有安全网关（片区白名单 + 时长钳制 +
  危险词拦截 + 人工确认），与规则解析共用同一条安全链路；
* 本模块任何异常都不向上抛（决策辅助不能拖垮主链路），失败自动回退规则解析；
* 单例 + 懒加载：首次真正用到时才 import onnxruntime / 建立 HTTP 连接。
"""
import json
import os
import threading
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 探测失败后的退避重试间隔（秒）：模型/服务后补部署时，不用重启服务器就能自动生效
_RETRY_AFTER = 30.0

_LOCK = threading.RLock()
_STATE = {
    'engine': None,        # 引擎实例 | None（未探测）| False（探测过但不可用）
    'mode': None,          # 'local' | 'http' | 'none'
    'info': {},            # 引擎自述信息（模型/后端/精度/provider）
    'last_error': '',
    'calls': 0,
    'errors': 0,
    'consecutive_errors': 0,
    'last_ms': None,
    'ms_sum': 0.0,
    'checked_at': 0.0,
}
_CFG_CACHE = None


# ---------------------------------------------------------------- 配置
def _cfg():
    """读 ``config/configs.yaml`` 的 EDGE 段。

    优先复用 app.state 里已加载的配置（与 GUI/服务器同源）；
    单独 import（如测试/脚本）时退回直接解析 YAML，避免拉起重量级 app.state。
    """
    global _CFG_CACHE
    if _CFG_CACHE is not None:
        return _CFG_CACHE
    cfg = {}
    try:
        from app.state import config as _c
        cfg = dict(_c.get('EDGE', {}) or {})
    except Exception:
        try:
            from tool.parser import get_config as _get
            cfg = dict(_get(os.path.join(ROOT, 'config', 'configs.yaml')).get('EDGE', {}) or {})
        except Exception:
            cfg = {}
    _CFG_CACHE = cfg
    return cfg


def _reset_cfg_cache():
    """测试用：清掉配置缓存。"""
    global _CFG_CACHE
    _CFG_CACHE = None


def _model_dir(cfg=None):
    cfg = cfg if cfg is not None else _cfg()
    p = str(cfg.get('model_dir') or './edge_models/jev-int8')
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def _base_url(cfg=None):
    cfg = cfg if cfg is not None else _cfg()
    return str(cfg.get('base_url') or 'http://127.0.0.1:8009').rstrip('/')


# ---------------------------------------------------------------- 引擎实现
class _LocalEngine:
    """进程内引擎：直接调用仓库自带的 edgejev 包（运行时只需 onnxruntime/tokenizers/numpy）。"""

    def __init__(self, model_dir, threads=0):
        from edgejev import Agent                      # 懒加载：没装 onnxruntime 时不炸 import
        kw = {}
        if threads and int(threads) > 0:
            kw['threads'] = int(threads)
        self.ag = Agent(model_dir, **kw)
        self.model_dir = model_dir

    def system_one(self, state, questions, timeout=None):
        return self.ag.system_one(state, questions)    # 本地推理，天然受 timeout 保护有限

    def describe(self):
        cfg = getattr(self.ag, 'cfg', {}) or {}
        return {
            'model_name': getattr(self.ag, 'model_name', None),
            'backend': cfg.get('backend'),
            'precision': cfg.get('precision'),
            'provider': getattr(self.ag, 'provider_note', None),
            'model_dir': self.model_dir,
        }


class _HttpEngine:
    """HTTP 引擎：连 ``edgejev serve`` 的官方协议 ``POST /v1/systemone``。

    用途：把引擎跑在独立进程 / 独立 venv（甚至别的内网机器）时，
    CropGuard 主进程不必装 onnxruntime，只需一个地址。
    """

    def __init__(self, base_url, api_key='', timeout=5.0):
        self.base_url = base_url
        self.api_key = api_key or ''
        self.timeout = float(timeout or 5.0)

    def _request(self, path, payload=None, method='GET'):
        import urllib.request
        url = self.base_url + path
        data = None
        headers = {'Accept': 'application/json'}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json; charset=utf-8'
        if self.api_key:
            headers['Authorization'] = 'Bearer %s' % self.api_key
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        # 别让系统代理把 127.0.0.1 也代理走（本机代理软件常见坑）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode('utf-8') or '{}')

    def system_one(self, state, questions, timeout=None):
        if timeout:
            self.timeout = float(timeout)
        return self._request('/v1/systemone', {'state': state, 'questions': questions}, 'POST')

    def describe(self):
        try:
            ready = self._request('/ready')
        except Exception:
            ready = {}
        return {
            'model_name': ready.get('model'),
            'backend': ready.get('backend'),
            'precision': ready.get('precision'),
            'provider': ready.get('provider'),
            'base_url': self.base_url,
        }


# ---------------------------------------------------------------- 探测 / 状态
def _probe_local(cfg):
    model_dir = _model_dir(cfg)
    need = ['edgejev.json', 'model.onnx', 'tokenizer.json']
    missing = [f for f in need if not os.path.exists(os.path.join(model_dir, f))]
    if missing:
        raise FileNotFoundError(
            '模型目录 %s 缺少 %s（用 python scripts/build_edge_model.py 构建）'
            % (model_dir, '、'.join(missing)))
    return _LocalEngine(model_dir, threads=cfg.get('threads') or 0)


def _probe_http(cfg):
    eng = _HttpEngine(_base_url(cfg), cfg.get('api_key') or '',
                      timeout=cfg.get('timeout') or 5.0)
    eng._request('/ready')      # 探活：连不上会抛
    return eng


def _detect(force=False):
    """探测引擎（带结果缓存 + 失败退避重试）。返回 (engine, mode, error)。"""
    cfg = _cfg()
    with _LOCK:
        if not bool(cfg.get('enabled', True)):
            return None, 'none', 'EDGE.enabled=false（已在配置里关闭）'
        if not force and _STATE['engine'] is not None and _STATE['engine'] is not False:
            return _STATE['engine'], _STATE['mode'], ''
        now = time.time()
        if not force and _STATE['engine'] is False and (now - _STATE['checked_at']) < _RETRY_AFTER:
            return None, 'none', _STATE['last_error']      # 退避期内不重复探测

        mode = str(cfg.get('mode') or 'auto').lower()
        errors = []

        def _try(kind):
            try:
                eng = _probe_local(cfg) if kind == 'local' else _probe_http(cfg)
                return eng, kind, eng.describe()
            except Exception as exc:
                errors.append('%s: %s' % (kind, str(exc)[:220]))
                return None, None, None

        order = {'auto': ['local', 'http'], 'local': ['local'], 'http': ['http']}.get(mode, ['local', 'http'])
        for kind in order:
            eng, k, info = _try(kind)
            if eng is not None:
                _STATE.update(engine=eng, mode=k, info=info or {}, last_error='',
                              checked_at=now, consecutive_errors=0)
                print('[EDGE] 引擎就绪 mode=%s %s' % (k, info), flush=True)
                return eng, k, ''

        _STATE.update(engine=False, mode='none', info={},
                      last_error='；'.join(errors) or '未找到可用引擎', checked_at=now)
        print('[EDGE] 引擎不可用：%s' % _STATE['last_error'], flush=True)
        return None, 'none', _STATE['last_error']


def status(refresh=False, probe=True):
    """引擎状态（给 ``GET /api/edge/status`` 与前端状态条用）。"""
    cfg = _cfg()
    engine, mode, err = (None, 'none', '') if not probe else _detect(force=refresh)
    with _LOCK:
        calls, errors = _STATE['calls'], _STATE['errors']
        last_ms = _STATE['last_ms']
        avg_ms = round(_STATE['ms_sum'] / calls, 1) if calls else None
        info = dict(_STATE['info'])
    model_dir = _model_dir(cfg)
    hint = ''
    if mode == 'none':
        if not os.path.exists(model_dir):
            hint = ('未找到模型目录 %s：先跑 python scripts/build_edge_model.py 构建，'
                    '或启动 edgejev serve 并配置 EDGE.base_url' % model_dir)
        else:
            hint = '模型目录已就位但引擎起不来，看 last_error'
    return {
        'success': True,
        'enabled': bool(cfg.get('enabled', True)),
        'available': engine is not None,
        'mode': mode,
        'model_dir': model_dir,
        'base_url': _base_url(cfg),
        'timeout': float(cfg.get('timeout') or 5.0),
        'prefer_for_commands': bool(cfg.get('prefer_for_commands', True)),
        'calls': calls,
        'errors': errors,
        'last_ms': last_ms,
        'avg_ms': avg_ms,
        'last_error': err or '',
        'hint': hint,
        'checked_at': datetime.now().isoformat(timespec='seconds'),
        **info,
    }


def available():
    return _detect()[0] is not None


def reset():
    """测试/现场重载用：丢弃缓存的引擎与配置，下次调用重新探测。"""
    with _LOCK:
        _STATE.update(engine=None, mode=None, info={}, last_error='',
                      checked_at=0.0, consecutive_errors=0)
    _reset_cfg_cache()


# ---------------------------------------------------------------- 通用决策
def decide(state, questions, timeout=None):
    """跑一次类型化决策。返回统一结构，**从不抛异常**。

    questions 用官方格式：``{qid: {"type": "choice|score|noul",
    "instructions": "...", "criteria": {...}|[...]}}``
    """
    engine, mode, err = _detect()
    if engine is None:
        return {'success': False, 'available': False, 'mode': 'none', 'error': err}
    t0 = time.time()
    try:
        out = engine.system_one(state, questions, timeout=timeout)
        ms = round((time.time() - t0) * 1000, 1)
        with _LOCK:
            _STATE['calls'] += 1
            _STATE['last_ms'] = ms
            _STATE['ms_sum'] += ms
            _STATE['consecutive_errors'] = 0
        return {'success': True, 'available': True, 'mode': mode,
                'answers': out.get('answers') or {},
                'usage': out.get('usage') or {},
                'model': out.get('model'),
                'latency_ms': ms}
    except Exception as exc:
        with _LOCK:
            _STATE['errors'] += 1
            _STATE['consecutive_errors'] += 1
            _STATE['last_error'] = '%s: %s' % (type(exc).__name__, str(exc)[:220])
            if _STATE['consecutive_errors'] >= 3:
                _STATE['engine'] = None        # 连续失败：丢弃引擎，下次重新探测
                _STATE['last_error'] += '（连续失败 3 次，已重置引擎待重探）'
        return {'success': False, 'available': True, 'mode': mode,
                'error': _STATE['last_error'], 'latency_ms': round((time.time() - t0) * 1000, 1)}


# ---------------------------------------------------------------- 指令解析（喷洒）
# ⚠️ 实测校准（见 edge_models/README.md 的探针记录）：
# laya-multilingual 是"路由/分类"向的小模型，对**自定义类型化问题**属于零样本，
# 明文局限（模型卡原文）：typed-decisions zero-shot 近随机、score 档位最弱、noul 偏保守。
# 因此这里的问法做了两件事：
#   1) instructions 用英文短句（对齐其训练口径；实测比中文问法明显稳定）；
#   2) criteria 用"动词短语列举"而不是长描述（实测关键词式描述更稳）。
# 且指令解析的最终口径是 **规则优先 + EdgeJev 兜底（带置信度闸门）**：
# 规则能解析就用规则（确定性、与历史行为一致）；规则拿不准时才让模型补位，
# 且只在置信度足够高时采信（见 combine_command）。安全闸门永远由规则侧主导。
COMMAND_QUESTIONS = {
    'intent': {
        'type': 'choice',
        'instructions': 'What does the user want to do with the spraying device?',
        'criteria': {
            'spray': 'start spraying, open the valve, water the field',
            'stop': 'stop spraying, close the device',
            'other': 'ask a question or chat',
        },
    },
    'group': {
        'type': 'choice',
        'instructions': 'Which zone is mentioned?',
        'criteria': {
            'g1': 'zone A, plot 1',
            'g2': 'zone B, plot 2',
            'all': 'all zones, everywhere',
            'none': 'no zone mentioned',
        },
    },
    'seconds': {
        'type': 'score',
        'instructions': 'How many seconds of spraying does the user ask for?',
        'criteria': ['1 second', '2 seconds', '3 seconds', '4 seconds', '5 seconds or more'],
    },
    'unsafe': {
        'type': 'noul',
        'instructions': 'Does the user ask for a dangerous operation: keep spraying without '
                        'stop, increase the dose, or bypass the safety limit?',
        'criteria': {'true': 'yes, dangerous', 'false': 'no, normal'},
    },
}

# 兜底闸门（只在规则解析失败时才用到 EdgeJev，且必须足够自信）
# 阈值来自 2026-10-16 的实测校准（本地 13 条指令集 + 停止类专项）：
#   * 真·停止改写（规则漏判）的 stop 概率 ≥0.93（把泵关了吧 0.934 / 把水停了 0.964 / 给B区关一下 0.974）
#   * 假·停止（真意是"开一会儿"）的 stop 概率 ≤0.76（把A区的水阀开一小会儿 0.756）
#   → 停止兜底取 0.85，落在两者之间的空档里；
#   * 真·喷洒指令的 stop 概率 ≤0.33，不会被误判成停止。
RESCUE_INTENT_MIN = 0.62        # 意图置信度下限（spray 兜底）
RESCUE_STOP_MIN = 0.85          # 停止类兜底要求更自信（"再来两秒"这类误判要拦住）
RESCUE_GROUP_MIN = 0.38         # 片区置信度下限（低于此宁可回"未指定片区"）

# 规则侧解析成功时不做兜底；规则侧的这些"失败"才允许兜底：
#   空 reason（无喷洒词）/ 没有识别到喷洒指令 / 未指定片区
# 安全类拒绝（含危险词）**绝不兜底** —— EdgeJev 不参与安全判定。
_RESCUEABLE_REASONS = ('', '没有识别到喷洒类指令', '未指定片区')


def _edge_view(answers):
    """截取给前端展示的决策摘要（choices/概率/noul/score），隐去冗长内部结构。"""
    view = {}
    for qid, a in (answers or {}).items():
        if not isinstance(a, dict):
            continue
        item = {'type': a.get('type')}
        for k in ('choice', 'probabilities', 'score', 'legend', 'noul', 'confidence'):
            if k in a:
                item[k] = a[k]
        view[qid] = item
    return view


def map_command_answers(text, answers, max_duration=5,
                        parse_duration=None, block_words=None):
    """把 EdgeJev 的 answers **原样**映射成 ``ai_gateway.parse_command`` 的同一契约。

    这是"模型怎么看这句话"的直译（不做置信度过滤）——
    用于 /api/edge/parse_command 的展示与对照；
    真正给 /api/ask_command 用的口径在 combine_command（规则优先 + 兜底闸门）。

    parse_duration: 复用 ``ai_gateway._parse_duration``（文本 -> (钳制后, 原始)）;
    block_words:    复用 ``ai_gateway._BLOCK_WORDS``。
    """
    answers = answers or {}
    text = str(text or '')
    t = text.replace(' ', '').lower()

    def _choice(qid):
        a = answers.get(qid) or {}
        return a.get('choice'), float(a.get('confidence') or 0)

    intent, _ci = _choice('intent')
    group, _cg = _choice('group')
    unsafe_a = answers.get('unsafe') or {}
    unsafe = float(unsafe_a.get('noul') or 0)
    seconds_a = answers.get('seconds') or {}
    try:
        sec_by_model = int(round(float(seconds_a.get('score') or 0))) + 1
    except Exception:
        sec_by_model = 3

    result = {'intent': 'unknown', 'group': None, 'duration': None, 'valid': False,
              'needs_confirm': True, 'max_duration': int(max_duration),
              'reason': '', 'reply': '', 'engine': 'edge'}

    # ---- 停止 ----
    if intent == 'stop':
        result.update(intent='stop', group='all', duration=0, valid=True,
                      reply='已解析为"停止喷洒"意图，请确认后执行。')
        result['unsafe'] = round(unsafe, 3)
        return result

    # ---- 非喷洒指令 ----
    if intent != 'spray':
        result.update(reason='没有识别到喷洒类指令',
                      reply='我没有识别到喷洒指令。可以说"给片区A喷3秒"。')
        result['unsafe'] = round(unsafe, 3)
        return result

    # ---- 危险表述：命中危险词表就拒绝（词表是确定性安全底线，模型判危只作提示）----
    result['unsafe'] = round(unsafe, 3)
    hit = ''
    for kw in (block_words or []):
        if kw in t:
            hit = kw
            break
    if hit:
        result.update(intent='spray', valid=False,
                      reason='含不安全表述"%s"' % hit,
                      reply='该指令不符合安全规则（雾化片单次喷淋有硬上限），'
                            '已拒绝。需要请手动点选片区分组与时长。')
        return result

    # ---- 片区：必须明确，绝不允许猜 ----
    if group not in ('g1', 'g2', 'all'):
        result.update(valid=False, reason='未指定片区',
                      reply='请指定片区：A 片区(g1)、B 片区(g2) 或全体喷洒(all)。')
        return result

    # ---- 时长：正则优先（与规则解析同口径），模型档位兜底 ----
    capped, _raw = (int(sec_by_model), int(sec_by_model))
    if callable(parse_duration):
        capped, _raw = parse_duration(text, default=sec_by_model,
                                      max_duration=int(max_duration))
    note = ''
    if _raw != capped:
        note = '（请求 %s 秒已按安全上限钳制为 %s 秒）' % (_raw, capped)

    result.update(intent='spray', group=group, duration=capped, valid=True, reason='',
                  reply='解析为：给 %s 喷洒 %s 秒%s。请确认后执行。' % (group, capped, note))
    return result


def parse_command(text, max_duration=5, parse_duration=None, block_words=None, rule_parser=None):
    """EdgeJev 指令解析（只解析，不执行）。

    返回里同时带：
      * parsed      —— 模型口径的直译（不过置信度闸门）；
      * rule        —— 规则解析结果（给了 rule_parser 才有）；
      * combined    —— /api/ask_command 实际采用的"规则优先 + 兜底"结果。
    """
    out = decide(text, COMMAND_QUESTIONS)
    rule_out = rule_parser(text, max_duration=max_duration) if callable(rule_parser) else None
    if not out.get('success'):
        return {'success': False, 'available': out.get('available', False),
                'mode': out.get('mode', 'none'), 'error': out.get('error', ''),
                'rule': rule_out}
    mapped = map_command_answers(text, out.get('answers'), max_duration=max_duration,
                                 parse_duration=parse_duration, block_words=block_words)
    return {'success': True, 'available': True, 'mode': out.get('mode'),
            'latency_ms': out.get('latency_ms'), 'model': out.get('model'),
            'usage': out.get('usage'), 'answers': _edge_view(out.get('answers')),
            'parsed': mapped, 'rule': rule_out,
            'combined': combine_command(rule_out, out, text, max_duration=max_duration,
                                        parse_duration=parse_duration, block_words=block_words)}


def combine_command(rule_out, edge_out, text, max_duration=5,
                    parse_duration=None, block_words=None):
    """规则优先 + EdgeJev 兜底（带置信度闸门）—— /api/ask_command 的最终口径。

    * 规则解析出 valid 结果 -> 直接用规则（确定性；与历史行为完全一致）；
    * 规则失败且属于"可兜底"的失败（没识别到 / 未指定片区）-> 让 EdgeJev 补位，
      但必须过置信度闸门（RESCUE_INTENT_MIN / RESCUE_STOP_MIN / RESCUE_GROUP_MIN）；
    * 安全类拒绝（危险词/危险操作）-> 直接沿用规则拒绝，**EdgeJev 无权翻案**；
    * EdgeJev 不可用/低置信 -> 沿用规则结论。

    返回同 parse_command 契约 + engine 字段（rule / edge）标注来源。
    """
    if not rule_out:
        # 没有规则结果（调用方没给 rule_parser）：用与规则侧一致的口径起底
        rule_out = {'intent': 'unknown', 'group': None, 'duration': None, 'valid': False,
                    'reason': '没有识别到喷洒类指令',
                    'reply': '我没有识别到喷洒指令。可以说"给片区A喷3秒"。'}
    r = dict(rule_out)
    r.setdefault('max_duration', int(max_duration))
    r['needs_confirm'] = True
    r['engine'] = 'rule'
    edges = (edge_out or {})
    if not edges.get('success'):
        return r

    answers = edges.get('answers') or {}
    int_a = answers.get('intent') or {}
    grp_a = answers.get('group') or {}
    intent = int_a.get('choice')
    int_p = max((int_a.get('probabilities') or {}).values(), default=0) if int_a else 0
    group = grp_a.get('choice')
    grp_p = max((grp_a.get('probabilities') or {}).values(), default=0) if grp_a else 0

    # 规则已成功 or 安全类拒绝 -> 不兜底（安全类拒绝是硬边界）
    if r.get('valid'):
        return r
    if r.get('reason') not in _RESCUEABLE_REASONS:
        return r

    def _with_edge(base):
        base = dict(base)
        base['engine'] = 'edge'
        base['edge_confidence'] = {'intent': round(float(int_p), 3),
                                   'group': round(float(grp_p), 3)}
        return base

    if intent == 'stop' and int_p >= RESCUE_STOP_MIN:
        # 停止类兜底：物理上安全（最多是"没喷"），但仍要求足够自信
        return _with_edge({'intent': 'stop', 'group': 'all', 'duration': 0, 'valid': True,
                           'max_duration': int(max_duration), 'needs_confirm': True,
                           'reason': '',
                           'reply': '已解析为"停止喷洒"意图（本地决策模型补充识别），请确认后执行。'})

    if intent == 'spray' and int_p >= RESCUE_INTENT_MIN:
        if group in ('g1', 'g2', 'all') and grp_p >= RESCUE_GROUP_MIN:
            capped, raw = 3, 3
            if callable(parse_duration):
                capped, raw = parse_duration(text, default=3, max_duration=int(max_duration))
            note = ('（请求 %s 秒已按安全上限钳制为 %s 秒）' % (raw, capped)) if raw != capped else ''
            return _with_edge({'intent': 'spray', 'group': group, 'duration': capped,
                               'valid': True, 'max_duration': int(max_duration),
                               'needs_confirm': True, 'reason': '',
                               'reply': '解析为：给 %s 喷洒 %s 秒%s'
                                        '（本地决策模型补充识别）。请确认后执行。'
                                        % (group, capped, note)})
        # 意图可信但片区没给/不敢认 -> 明确"未指定片区"（与规则话术一致）
        return _with_edge({'intent': 'spray', 'group': None, 'duration': None, 'valid': False,
                           'max_duration': int(max_duration), 'needs_confirm': True,
                           'reason': '未指定片区',
                           'reply': '请指定片区：A 片区(g1)、B 片区(g2) 或全体喷洒(all)。'})

    return r


# ---------------------------------------------------------------- 检测结果研判（辅助）
# ⚠️ 实测（2026-10-16）：laya-multilingual 对这类"自造类型化问题"是零样本，
# 输出只能当**参考信号**，不能当结论 —— 页面上如实标注"实验性"。
# 问法仍按最稳的格式写（英文 instructions + 动词短语式 criteria）。
DETECTION_QUESTIONS = {
    'action': {
        'type': 'choice',
        'instructions': 'What should the operator do now?',
        'criteria': {
            'spray': 'spray pesticide immediately, serious pest outbreak detected',
            'watch': 'keep monitoring, inspect the field again later',
            'none': 'no action needed, the field is fine',
        },
    },
    'urgency': {
        'type': 'score',
        'instructions': 'How urgent is this situation?',
        'criteria': ['not urgent at all', 'slightly urgent', 'moderately urgent',
                     'quite urgent', 'extremely urgent'],
    },
    'spray_needed': {
        'type': 'noul',
        'instructions': 'Is there a serious pest infestation that requires spraying right now?',
        'criteria': {'true': 'yes, serious infestation, many pests, high risk',
                     'false': 'no pests or only a few, low risk, do not spray'},
    },
}


def _state_text_of_detections(detections, risk=None):
    dets = list(detections or [])[:12]
    if not dets:
        return '当前画面未检测到任何害虫目标。系统风险等级：无风险。'
    parts = []
    for d in dets:
        if isinstance(d, dict):
            name = d.get('chinese_name') or d.get('class_name') or '未知'
            conf = d.get('confidence')
            parts.append('%s(置信度%s)' % (name, round(float(conf), 2) if conf is not None else '?'))
        else:
            parts.append(str(d))
    level = ''
    if isinstance(risk, dict):
        level = risk.get('level') or risk.get('detail') or ''
    elif risk:
        level = str(risk)
    return '本次检测到害虫目标 %d 个：%s。系统风险等级：%s。' % (len(dets), '、'.join(parts), level or '未知')


def decide_detection(detections, risk=None):
    """检测结果 -> 类型化处置研判（**辅助参考**，不驱动硬件，不改动既有风险分级口径）。"""
    out = decide(_state_text_of_detections(detections, risk), DETECTION_QUESTIONS)
    if out.get('success'):
        out['state_text'] = _state_text_of_detections(detections, risk)
        out['note'] = '类型化决策结果仅供参考；自动联动仍按 SPRAY.risk_map 的既有口径执行。'
    return out
