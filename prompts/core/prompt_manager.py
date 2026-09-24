"""
提示词管理器 - 加载和管理各AI模型的提示词模板
"""
import os

# 获取模板目录路径
_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')

# 默认提示词模板（当模板文件不存在时使用）
_DEFAULT_PROMPTS = {
    'zhipu': '你是一位资深的农业病虫害防治专家。请基于检测到的病虫害信息，生成一份约1500字的详细防治技术报告。报告应包括：1.病虫害特征描述 2.发生规律分析 3.综合防治措施（农业防治、物理防治、生物防治、化学防治）4.预防建议。请使用专业、科学、严谨的语言。',
    'qwen': '你是一位资深的农业病虫害防治专家。请基于检测到的病虫害信息，生成一份约1500字的详细防治技术报告。报告应包括：1.病虫害特征描述 2.发生规律分析 3.综合防治措施（农业防治、物理防治、生物防治、化学防治）4.预防建议。请使用专业、科学、严谨的语言。',
    'deepseek': '你是一位资深的农业病虫害防治专家。请基于检测到的病虫害信息，生成一份约1500字的详细防治技术报告。报告应包括：1.病虫害特征描述 2.发生规律分析 3.综合防治措施（农业防治、物理防治、生物防治、化学防治）4.预防建议。请使用专业、科学、严谨的语言。',
    'openai': '你是一位资深的农业病虫害防治专家。请基于检测到的病虫害信息，生成一份约1500字的详细防治技术报告。报告应包括：1.病虫害特征描述 2.发生规律分析 3.综合防治措施（农业防治、物理防治、生物防治、化学防治）4.预防建议。请使用专业、科学、严谨的语言。',
    'doubao': '你是一位资深的农业病虫害防治专家。请基于检测到的病虫害信息，生成一份约1500字的详细防治技术报告。报告应包括：1.病虫害特征描述 2.发生规律分析 3.综合防治措施（农业防治、物理防治、生物防治、化学防治）4.预防建议。请使用专业、科学、严谨的语言。',
    'qianfan': '你是一位资深的农业病虫害防治专家。请基于检测到的病虫害信息，生成一份约1500字的详细防治技术报告。报告应包括：1.病虫害特征描述 2.发生规律分析 3.综合防治措施（农业防治、物理防治、生物防治、化学防治）4.预防建议。请使用专业、科学、严谨的语言。',
    'default': '你是一位资深的农业病虫害防治专家。请基于检测到的病虫害信息，生成一份约1500字的详细防治技术报告。报告应包括：1.病虫害特征描述 2.发生规律分析 3.综合防治措施（农业防治、物理防治、生物防治、化学防治）4.预防建议。请使用专业、科学、严谨的语言。',
}


class PromptManager:
    """提示词管理器"""

    def __init__(self):
        self._prompts = {}
        self._load_prompts()

    def _load_prompts(self):
        """从模板文件加载提示词，如果文件不存在则使用默认值"""
        for model_name, default_text in _DEFAULT_PROMPTS.items():
            template_path = os.path.join(_TEMPLATES_DIR, f'{model_name}.txt')
            if os.path.exists(template_path):
                try:
                    with open(template_path, 'r', encoding='utf-8') as f:
                        self._prompts[model_name] = f.read().strip()
                except Exception:
                    self._prompts[model_name] = default_text
            else:
                self._prompts[model_name] = default_text

    def get_prompt(self, model_name: str = 'default') -> str:
        """获取指定模型的提示词"""
        if model_name not in self._prompts:
            return self._prompts.get('default', '')
        return self._prompts[model_name]

    def reload(self):
        """重新加载所有提示词"""
        self._load_prompts()


# 全局单例
prompt_manager = PromptManager()
