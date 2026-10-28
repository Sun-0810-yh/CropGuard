import cv2
import xlwt


def box_label(cls_name):
    """检测框上显示的英文名：corn_borer → Corn borer（只管框上的字，列表/播报/备案中文名不受影响）。"""
    s = str(cls_name or '').replace('_', ' ').strip()
    return s[:1].upper() + s[1:]


def result_info_format(result_info, box, score, cls_name):
    '''
        格式组合
    '''
    # 类别
    result_info['cls_name'] = cls_name
    # 置信度
    result_info['score'] = round(score, 2)
    result_info['label_xmin_v'] = int(box[0])
    result_info['label_ymin_v'] = int(box[1])
    result_info['label_xmax_v'] = int(box[2])
    result_info['label_ymax_v'] = int(box[3])

    return result_info


def format_data(results):
    '''
    整理模型的识别结果
    '''
    lst_results = []
    for i, r in enumerate(results):
        # r.show()
        boxes = r.boxes.xyxy.cpu().numpy().tolist()
        conf = r.boxes.conf.cpu().numpy().tolist()
        cls = r.boxes.cls.cpu().numpy().tolist()
        names = r.names
        # speed = r.speed
        # # 转成 s
        # consum_time = round((speed['preprocess'] + speed['inference'] + speed['postprocess']) / 1000, 3)
        for box, con, c in zip(boxes, conf, cls):
            lst_results.append([names[c], round(con, 2), box])
    return lst_results


def writexls(DATA, path):

    wb = xlwt.Workbook()
    ws = wb.add_sheet('Data')
    for i, Data in enumerate(DATA):
        for j, data in enumerate(Data):
            ws.write(i, j, str(data))
    wb.save(path)


def writecsv(DATA, path):
    try:
        f = open(path, 'w', encoding='utf8')
        for data in DATA:
            f.write(','.join('%s' % dat for dat in data) + '\n')
        f.close()
    except Exception as e:
        print(e)


def compute_color_for_labels(label):
    """
    Simple function that adds fixed color depending on the class.
    This version improves color distinctiveness by using the golden ratio.
    """
    # 黄金比例
    golden_ratio_conjugate = 0.618033988749895
    # 初始色调（可以选择任意数值）
    hue = (label * golden_ratio_conjugate) % 1

    # 使用色调转换为RGB值
    def hsv_to_rgb(h, s, v):
        i = int(h * 6)
        f = h * 6 - i
        p = v * (1 - s)
        q = v * (1 - f * s)
        t = v * (1 - (1 - f) * s)
        r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i % 6]
        return int(r * 255), int(g * 255), int(b * 255)

    # 调整饱和度和亮度
    saturation = 0.7
    value = 0.95

    color = hsv_to_rgb(hue, saturation, value)
    return color


def _text_color_for(background):
    """按背景亮度选黑字/白字 —— 浅底黑字、深底白字，任何底色都看得清。"""
    b, g, r = int(background[0]), int(background[1]), int(background[2])
    luma = 0.114 * b + 0.587 * g + 0.299 * r
    return (0, 0, 0) if luma > 140 else (255, 255, 255)


# 标签字号取"图像长边"的固定比例，这样**不同分辨率的图缩到预览窗口后，
# 屏幕上的字号是一样的**（预览是按长边等比缩放，所以按长边算才对）。
# 现场踩过的坑：原来 font_scale 写死 0.5，720p 勉强能看，
# 但 1944x2580 的相机原图缩到 660px 预览后标签只剩 2~3px，根本读不出类别和置信度。
_LABEL_TEXT_RATIO = 0.032      # 文字高度 ≈ 长边 × 3.2%
_LABEL_MIN_SCALE = 0.6
_LABEL_MAX_SCALE = 4.0
_HERSHEY_UNIT_HEIGHT = 22.0    # FONT_HERSHEY_SIMPLEX 在 font_scale=1 时的大写字母高度


def _label_metrics(image):
    """按图像尺寸算标签的 (font_scale, thickness, pad)。"""
    h, w = image.shape[:2]
    longest = max(h, w) or 720
    scale = (_LABEL_TEXT_RATIO * longest) / _HERSHEY_UNIT_HEIGHT
    scale = max(_LABEL_MIN_SCALE, min(_LABEL_MAX_SCALE, scale))
    thickness = max(1, int(round(scale * 1.6)))
    pad = max(3, int(round(scale * 6)))
    return scale, thickness, pad


def draw_info(frame, results):
    """在图上画检测框 + 「类别 置信度」标签。

    标签随图像尺寸等比放大（见 `_label_metrics`），并按底色明暗自动选黑/白字，
    保证不同分辨率、不同背景的图上都看得清。
    """
    if not results:
        return frame

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale, thickness, pad = _label_metrics(frame)
    img_h, img_w = frame.shape[:2]

    for i, bbox in enumerate(results):
        cls_name = bbox[0]
        conf = bbox[1]
        box = bbox[2]

        color = compute_color_for_labels(i)
        x1, y1, x2, y2 = (int(v) for v in box)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

        try:
            text = f'{box_label(cls_name)} {float(conf):.2f}'
        except (TypeError, ValueError):
            text = f'{box_label(cls_name)} {conf}'
        (tw, th), _ = cv2.getTextSize(text, font, font_scale, thickness)

        # 标签默认贴在框上沿；顶到画面外就改画到框内侧，避免被裁掉
        top = y1 - th - 2 * pad
        if top < 0:
            top = min(y1 + pad, max(0, img_h - th - 2 * pad))
        left = max(0, min(x1, img_w - tw - 2 * pad))

        cv2.rectangle(frame, (left, top), (left + tw + 2 * pad, top + th + 2 * pad),
                      color, cv2.FILLED)
        cv2.putText(frame, text, (left + pad, top + th + pad - max(1, pad // 3)),
                    font, font_scale, _text_color_for(color), thickness, cv2.LINE_AA)

    return frame


def assess_risk(results, image_shape=None):
    """
    根据检测结果评估病害风险等级
    results: [[cls_name, conf, [x1,y1,x2,y2]], ...]
    image_shape: (h, w) 可选，用于计算覆盖面积
    返回: (risk_level, risk_color, risk_detail)
    """
    if not results:
        return '无风险', (0, 128, 0), '未检测到害虫，作物健康状况良好。'

    pest_count = len(results)
    # 计算平均置信度
    avg_conf = sum(r[1] for r in results) / pest_count

    # 102类全是农业害虫，直接用数量和置信度评估风险
    harmful_count = pest_count  # 全部视为害虫

    # 计算分布密度（基于边界框面积占比）
    area_ratio = 0
    if image_shape:
        img_area = image_shape[0] * image_shape[1]
        total_box_area = sum(
            (r[2][2] - r[2][0]) * (r[2][3] - r[2][1]) for r in results
        )
        area_ratio = total_box_area / img_area if img_area > 0 else 0

    # 风险等级判定（综合考虑数量、置信度、覆盖面积）
    if harmful_count >= 6 or area_ratio > 0.1 or (harmful_count >= 3 and avg_conf >= 0.7):
        level = '高风险'
        color = (0, 0, 255)  # 红色
        reasons = []
        if harmful_count >= 6:
            reasons.append(f'害虫数量多({harmful_count}个)')
        if area_ratio > 0.1:
            reasons.append(f'虫害覆盖面积较大({area_ratio:.1%})')
        if harmful_count >= 3 and avg_conf >= 0.7:
            reasons.append(f'置信度较高(均{avg_conf:.2f})')
        detail = '、'.join(reasons) + '，需立即采取综合防治措施！'
    elif harmful_count <= 2 and avg_conf < 0.5 and area_ratio < 0.05:
        level = '低风险'
        color = (0, 200, 255)  # 黄色
        detail = f'害虫数量少({harmful_count}个)，置信度较低，建议持续观察。'
    elif harmful_count <= 2:
        level = '低风险'
        color = (0, 200, 255)  # 黄色
        detail = f'检测到 {harmful_count} 个害虫，建议关注。'
    else:
        level = '中风险'
        color = (0, 140, 255)  # 橙色
        detail = f'害虫数量中等({harmful_count}个)，平均置信度{avg_conf:.2f}，建议采取防治措施。'

    return level, color, detail

