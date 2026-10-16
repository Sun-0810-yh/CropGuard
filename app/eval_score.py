# -*- coding: utf-8 -*-
"""现场测评评分口径（纯函数，GUI 与 Web 测评页 /eval 共用同一份）。

口径（2026-10-11 与用户对齐，见 config/configs.yaml 的 EVAL 段注释）：

    准确度 20 分 = 正确样本数 × points_per_sample（20 个样本 × 1 分）
    时效性 10 分 = time_full_score − max(0, 用时 − time_limit_sec) × overtime_penalty_per_sec
    总分   = 准确度 + 时效性（满分 30），扣分扣到 0 为止；未评判不计入正确。

为什么单独一个模块：GUI（main.py）与 Web 测评页（api_server.py 的 /eval + /api/eval/*）
都在"现场评分类"功能上用这个口径，放在一起才不会一边改了另一边漏改 ——
这个数字会被评委现场核对。
"""

# 现场口径默认值（与 config/configs.yaml EVAL 段默认一致；防被误改由测试钉住）
DEFAULTS = {
    'target': 20,
    'points_per_sample': 1.0,
    'time_limit': 180.0,
    'time_full': 10.0,
    'overtime_penalty': 0.5,
}


def compute_eval_score(records, elapsed, target=None, pps=None,
                       limit=None, time_full=None, penalty=None):
    """按现场评分口径算分。

    准确度 = min(正确数, target) × pps
    时效性 = max(0, time_full − max(0, 用时 − limit) × penalty)   # 扣完为止
    总分   = 准确度 + 时效性

    「未评判」不计入正确 —— 漏判只会少分，不会被误判成对。

    records: [{'verdict': '正确'|'错误'|None, ...}]（只读 verdict 字段）
    elapsed: 本轮总用时（秒）
    """
    target = DEFAULTS['target'] if target is None else target
    pps = DEFAULTS['points_per_sample'] if pps is None else pps
    limit = DEFAULTS['time_limit'] if limit is None else limit
    time_full = DEFAULTS['time_full'] if time_full is None else time_full
    penalty = DEFAULTS['overtime_penalty'] if penalty is None else penalty

    records = list(records or [])
    total = len(records)
    judged = sum(1 for r in records if (r or {}).get('verdict'))
    correct = sum(1 for r in records if (r or {}).get('verdict') == '正确')

    acc = min(correct, target) * pps
    elapsed = max(0.0, float(elapsed or 0.0))
    over = max(0.0, elapsed - limit)
    time_score = max(0.0, time_full - over * penalty)

    return {
        'total': total,
        'judged': judged,
        'correct': correct,
        'acc': round(acc, 1),
        'elapsed': round(elapsed, 1),
        'over': round(over, 1),
        'time_score': round(time_score, 1),
        'score': round(acc + time_score, 1),
        'finished': total >= target,
    }


def rubric_from_config(config):
    """从 configs.yaml 的 EVAL 段取口径参数（缺项用默认值）。

    注意配置键名与 DEFAULTS 键名不同（历史原因：配置用 target_samples 这类长名），
    这里显式映射，避免"某次改名后静默取默认值"。
    """
    cfg = (config or {}).get('EVAL', {}) or {}

    def _f(cfg_key, default):
        try:
            return float(cfg.get(cfg_key, default))
        except (TypeError, ValueError):
            return float(default)

    target = int(_f('target_samples', DEFAULTS['target']))
    pps = _f('points_per_sample', DEFAULTS['points_per_sample'])
    limit = _f('time_limit_sec', DEFAULTS['time_limit'])
    time_full = _f('time_full_score', DEFAULTS['time_full'])
    penalty = _f('overtime_penalty_per_sec', DEFAULTS['overtime_penalty'])

    return {
        'target': target,
        'pps': pps,
        'limit': limit,
        'time_full': time_full,
        'penalty': penalty,
        'acc_full': target * pps,
        'total_full': target * pps + time_full,
        'sample_dir': str(cfg.get('sample_dir') or './pictures20'),
        'report_dir': str(cfg.get('report_dir') or './output/eval_reports'),
    }


def score_with_rubric(records, elapsed, rubric):
    """用 rubric_from_config 的口径算分（Web 测评页走这条）。"""
    r = rubric or {}
    return compute_eval_score(
        records, elapsed,
        target=r.get('target'), pps=r.get('pps'), limit=r.get('limit'),
        time_full=r.get('time_full'), penalty=r.get('penalty'))


# ================================================================
# 测评页共用的纯逻辑（样本清单 + 报告行）—— 放在这里是为了可单测：
# api_server.py 会拉起 YOLO/相机等重初始化，测试没法直接 import 它。
# ================================================================

IMG_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.jfif', '.tif', '.tiff'}


def is_image_name(name):
    """按扩展名判断是否图片（样本目录与上传两条路共用同一口径）。"""
    import os
    return os.path.splitext(str(name or ''))[1].lower() in IMG_EXTS


def list_sample_images(directory, limit=500):
    """列出样本目录里的图片（按文件名排序；目录不存在返回空列表）。"""
    import os
    if not directory or not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        full = os.path.join(directory, name)
        if os.path.isfile(full) and is_image_name(name):
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            out.append({'name': name, 'size': size})
        if len(out) >= limit:
            break
    return out


def build_report_rows(records, elapsed, rubric):
    """构造评分报告行（与 GUI 导出的 csv 列对齐：逐样本 + 汇总）。

    records: [{no, name|path, time, detections:[{chinese_name|class_name, confidence}],
               consum, elapsed, verdict}]
    返回 (rows, score)。
    """
    r = rubric or {}
    score = score_with_rubric(records, elapsed, r)
    target = r.get('target', DEFAULTS['target'])
    acc_full = r.get('acc_full', DEFAULTS['target'] * DEFAULTS['points_per_sample'])
    time_full = r.get('time_full', DEFAULTS['time_full'])
    limit = r.get('limit', DEFAULTS['time_limit'])
    penalty = r.get('penalty', DEFAULTS['overtime_penalty'])
    total_full = r.get('total_full', acc_full + time_full)

    rows = [['编号', '图片路径', '输入时间', '识别结果(中文)', '目标数量',
             '推理耗时', '本轮用时(s)', '评判']]
    for rec in (records or []):
        names = []
        for d in (rec.get('detections') or []):
            cn = d.get('chinese_name') or d.get('class_name') or ''
            try:
                names.append('%s(%.2f)' % (cn, float(d.get('confidence') or 0)))
            except (TypeError, ValueError):
                names.append(str(cn))
        rows.append([
            rec.get('no'), rec.get('name') or rec.get('path') or '',
            rec.get('time') or '',
            '、'.join(names) or '无目标',
            len(rec.get('detections') or []),
            rec.get('consum') if rec.get('consum') is not None else '',
            rec.get('elapsed') if rec.get('elapsed') is not None else '',
            rec.get('verdict') or '未评判',
        ])
    rows.append([])
    rows.append(['===== 现场评测汇总（口径：准确度 %s + 时效性 %s = %s）====='
                 % (_fmt_num(acc_full), _fmt_num(time_full), _fmt_num(total_full))])
    rows.append(['采集样本数', '%s / %s' % (score['total'], _fmt_num(target))])
    rows.append(['已评判', score['judged']])
    rows.append(['识别正确', score['correct']])
    rows.append(['准确度得分', '%s / %s' % (_fmt_num(score['acc']), _fmt_num(acc_full))])
    rows.append(['本轮总用时(s)', _fmt_num(score['elapsed'])])
    rows.append(['基准时间(s)', _fmt_num(limit)])
    rows.append(['超时(s)', _fmt_num(score['over'])])
    rows.append(['时效性得分', '%s / %s' % (_fmt_num(score['time_score']), _fmt_num(time_full))])
    rows.append(['总分', '%s / %s' % (_fmt_num(score['score']), _fmt_num(total_full))])
    rows.append(['说明', '未评判不计入正确；时效性每超时 1 秒扣 %s，扣完为止。'
                         % _fmt_num(penalty)])
    return rows, score


def _fmt_num(v):
    """数字格式化：整数不带小数点（20 而不是 20.0），与 GUI 报告口径一致。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else ('%.1f' % f)
