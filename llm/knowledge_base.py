"""27 类农业害虫本地防治知识库。

作为本地大模型不可用时的兜底方案：秒回、零幻觉、100% 离线。
内容为通用农学防治框架，比赛前可对照权威植保资料校对补充。
检测到的害虫英文名与 config/configs.yaml 的 chinese_name 一致。
"""

PEST_KB = {
    'Cicadellidae': {
        'cn': '叶蝉科',
        'host': '水稻、小麦、蔬菜、果树等',
        'damage': '成虫与若虫刺吸叶片汁液，致叶片褪绿、出现白色斑点，严重时全叶枯黄；部分种类传播植物病毒病。',
        'control': '清除田间杂草减少虫源；利用黑光灯诱杀成虫；保护寄生蜂等天敌；药剂选用吡虫啉、噻虫嗪等。',
    },
    'Miridae': {
        'cn': '盲蝽科',
        'host': '棉花、果树、苜蓿等',
        'damage': '刺吸嫩芽、嫩叶与花蕾，致叶片穿孔、畸形、落蕾，影响产量与品质。',
        'control': '清除田边杂草；合理轮作；保护天敌；药剂选用高效氯氟氰菊酯等。',
    },
    'blister_beetle': {
        'cn': '芫菁',
        'host': '豆类、马铃薯、苜蓿等',
        'damage': '成虫取食叶片与花，成片危害；虫体含斑蝥素，勿徒手接触。',
        'control': '人工捕杀成虫（戴手套）；清洁田园；药剂选用菊酯类或敌百虫。',
    },
    'aphids': {
        'cn': '蚜虫',
        'host': '棉花、小麦、蔬菜、果树等多种作物',
        'damage': '群集嫩叶、嫩茎、花蕾吸食汁液，致叶片卷曲发黄、生长停滞；分泌蜜露诱发煤污病，并传播病毒。',
        'control': '黄板诱杀有翅蚜；银灰膜避蚜；保护瓢虫、食蚜蝇、草蛉等天敌；药剂选用吡虫啉、啶虫脒等。',
    },
    'mole_cricket': {
        'cn': '蝼蛄',
        'host': '多种旱地作物幼苗',
        'damage': '成虫与若虫在土中咬食种子、幼根，并挖掘隧道致幼苗根系架空枯死。',
        'control': '深耕灭茬；用炒香麦麸拌辛硫磷制成毒饵撒施；药剂灌根或毒土处理。',
    },
    'Locustoidea': {
        'cn': '蝗虫',
        'host': '禾本科作物与牧草',
        'damage': '成虫与若虫群集暴食叶片，大发生时可将整片作物吃光，造成毁灭性损失。',
        'control': '秋耕灭卵；保护鸟类等天敌；生物防治用绿僵菌、微孢子虫；暴发时药剂选用马拉硫磷、高效氯氟氰菊酯。',
    },
    'corn_borer': {
        'cn': '玉米螟',
        'host': '玉米、高粱、谷子等',
        'damage': '幼虫蛀食茎秆、雄穗与雌穗，造成茎秆折断、籽粒损失。',
        'control': '心叶末期撒施辛硫磷颗粒剂或 Bt 制剂灌心；利用性诱剂诱杀；秋后处理秸秆消灭越冬幼虫。',
    },
    'wireworm': {
        'cn': '金针虫',
        'host': '小麦、玉米、马铃薯等地下部',
        'damage': '幼虫在土中咬食种子、幼根与地下茎，造成缺苗断垄、薯块受害。',
        'control': '药剂拌种；毒土或毒饵处理土壤；合理轮作；深翻晒垡。',
    },
    'legume_blister_beetle': {
        'cn': '豆类芫菁',
        'host': '豆类作物',
        'damage': '成虫群集取食叶片与花器，危害豆类生长；虫体含斑蝥素。',
        'control': '人工捕杀（戴手套）；清洁田园；药剂选用菊酯类。',
    },
    'grub': {
        'cn': '蛴螬',
        'host': '多种作物的地下根茎',
        'damage': '金龟子幼虫，咬食根茎与块根块茎，致植株萎蔫枯死、薯块出现缺刻。',
        'control': '秋耕捡拾；药剂灌根或毒土；生物防治用白僵菌、绿僵菌；黑光灯诱杀成虫。',
    },
    'flax_budworm': {
        'cn': '亚麻蕾虫',
        'host': '亚麻',
        'damage': '幼虫蛀食花蕾与蒴果，影响结实。',
        'control': '合理轮作；花期前后药剂选用菊酯类防治。',
    },
    'Prodenia_litura': {
        'cn': '斜纹夜蛾',
        'host': '蔬菜、棉花、甘薯等',
        'damage': '幼虫暴食叶片，大龄幼虫可将叶片吃成网状或光杆，食性杂、繁殖快。',
        'control': '性诱剂诱杀成虫；人工摘除卵块与幼虫群；药剂选用甲维盐、虫螨腈、Bt 制剂。',
    },
    'beet_army_worm': {
        'cn': '甜菜夜蛾',
        'host': '甜菜、蔬菜等',
        'damage': '幼虫取食叶片，低龄群集、大龄暴食，抗药性较强。',
        'control': '清除杂草减少虫源；性诱剂诱杀；药剂轮换选用甲维盐、茚虫威、Bt 制剂。',
    },
    'Lycorma_delicatula': {
        'cn': '斑衣蜡蝉',
        'host': '葡萄、猕猴桃等果树',
        'damage': '若虫与成虫刺吸枝干汁液，分泌蜜露诱发煤污病，削弱树势。',
        'control': '冬季刮除枝干卵块；若虫期药剂选用吡虫啉、噻虫嗪；保护寄生天敌。',
    },
    'flea_beetle': {
        'cn': '跳甲',
        'host': '十字花科蔬菜',
        'damage': '成虫取食叶片成密集小孔，幼虫蛀食根部，苗期危害重。',
        'control': '清除田间杂草；合理轮作；药剂选用菊酯类或噻虫嗪。',
    },
    'tarnished_plant_bug': {
        'cn': '花盲蝽',
        'host': '棉花、果树、蔬菜等',
        'damage': '刺吸花蕾与嫩果，致落蕾、落果、果实畸形。',
        'control': '清除杂草减少虫源；保护天敌；药剂选用高效氯氟氰菊酯。',
    },
    'black_cutworm': {
        'cn': '黑切叶虫',
        'host': '多种作物幼苗',
        'damage': '幼虫夜间咬断幼苗茎基部，造成缺苗断垄。',
        'control': '清除杂草；清晨人工捕杀；毒饵诱杀；药剂选用高效氯氟氰菊酯灌根。',
    },
    'Ampelophaga': {
        'cn': '葡萄天蛾',
        'host': '葡萄',
        'damage': '幼虫取食叶片，大龄幼虫食量大，可将叶片吃光。',
        'control': '人工捕杀幼虫；黑光灯诱杀成虫；药剂选用菊酯类或苏云金杆菌。',
    },
    'peach_borer': {
        'cn': '桃蛀螟',
        'host': '桃、李等果树及玉米',
        'damage': '幼虫蛀食果实与茎秆，造成落果、烂果，影响品质。',
        'control': '果实套袋；性诱剂诱杀；及时清园处理虫果；药剂选用氯虫苯甲酰胺等。',
    },
    'lytta_polita': {
        'cn': '丽缘红姬甲',
        'host': '豆类作物',
        'damage': '成虫取食叶片与花器，危害豆类生长。',
        'control': '人工捕杀；清洁田园；药剂选用菊酯类。',
    },
    'cabbage_army_worm': {
        'cn': '甘蓝夜蛾',
        'host': '甘蓝、白菜等十字花科蔬菜',
        'damage': '幼虫取食叶片，钻蛀叶球，造成产量与品质下降。',
        'control': '性诱剂诱杀成虫；人工摘除卵块；药剂选用甲维盐、Bt 制剂。',
    },
    'alfalfa_plant_bug': {
        'cn': '苜蓿盲蝽',
        'host': '苜蓿、棉花等',
        'damage': '刺吸嫩芽嫩叶，致叶片皱缩、生长点受损。',
        'control': '清除杂草；合理轮作；保护天敌；药剂选用高效氯氟氰菊酯。',
    },
    'army_worm': {
        'cn': '粘虫',
        'host': '小麦、玉米、水稻等禾本科作物',
        'damage': '幼虫暴食叶片，具群集迁移习性，大发生时可将叶片吃光。',
        'control': '性诱剂或糖醋液诱杀成虫；药剂选用高效氯氟氰菊酯、Bt 制剂；保护天敌。',
    },
    'Potosiabre_vitarsis': {
        'cn': '光肩星天牛',
        'host': '果树、林木等',
        'damage': '成虫取食花果，幼虫（蛴螬）蛀食根部，削弱树势。',
        'control': '黑光灯诱杀成虫；幼虫期药剂灌根；结合秋耕拾虫。',
    },
    'Cicadella_viridis': {
        'cn': '绿蝉',
        'host': '果树、林木等',
        'damage': '刺吸枝干与叶片汁液，分泌蜜露诱发煤污病，可传播病毒。',
        'control': '清除杂草；黑光灯诱杀；药剂选用吡虫啉、噻虫嗪。',
    },
    'yellow_cutworm': {
        'cn': '黄切叶虫',
        'host': '多种作物幼苗',
        'damage': '幼虫夜间咬断幼苗茎基部，造成缺苗断垄。',
        'control': '清除杂草；毒饵诱杀；药剂选用高效氯氟氰菊酯灌根。',
    },
    'rice_leaf_roller': {
        'cn': '水稻卷叶螟',
        'host': '水稻',
        'damage': '幼虫吐丝将叶片纵卷成苞并取食叶肉，致叶片枯白，影响光合作用。',
        'control': '性诱剂诱杀；合理密植与施肥；药剂选用氯虫苯甲酰胺、Bt 制剂。',
    },
}

# 无检测目标时的提示
NO_PEST_MSG = '未检测到害虫，作物健康状况良好，建议保持常规田间管理。'


def get_advice(pests, risk_level='中风险', risk_detail=''):
    """根据检测结果拼装离线防治报告。

    pests: [(英文名, 置信度), ...]，如 [('aphids', 0.85)]
    返回：拼装好的文本报告（秒回，无网络依赖）。
    """
    if not pests:
        return NO_PEST_MSG

    seen = set()
    lines = ['【病虫害诊断】']
    detected = []
    for name, conf, *_ in pests:
        if name in seen:
            continue
        seen.add(name)
        cn = PEST_KB.get(name, {}).get('cn', name)
        detected.append(f'{cn}（置信度 {conf:.2f}）')
    lines.append('检测到：' + '、'.join(detected))
    if risk_level:
        lines.append(f'风险等级：{risk_level}')
    if risk_detail:
        lines.append(f'风险详情：{risk_detail}')

    lines.append('')
    lines.append('【为害特征与防治建议】')
    for name, conf, *_ in pests:
        if name not in PEST_KB:
            continue
        info = PEST_KB[name]
        lines.append(f'◆ {info["cn"]}（为害 {info["host"]}）')
        lines.append(f'  为害：{info["damage"]}')
        lines.append(f'  防治：{info["control"]}')
        break  # 知识库兜底版仅详述首类，避免过长；多类混发交由大模型生成综合方案

    lines.append('')
    lines.append('【通用预防措施】')
    lines.append('1. 加强田间监测，早发现早防治；')
    lines.append('2. 合理轮作与清洁田园，减少虫源基数；')
    lines.append('3. 保护天敌，优先选用生物与物理防治；')
    lines.append('4. 科学用药，注意药剂轮换，延缓抗药性。')

    return '\n'.join(lines)
