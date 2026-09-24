"""本地大模型客户端 - 通过 Ollama 离线调用，不依赖互联网 API。

国赛要求离线运行、严禁调用外部在线推理服务，故用本地 Ollama 替代原来的
DeepSeek/OpenAI 等联网 API。上层调用方需在 Ollama 不可用时降级到本地知识库。
"""
import requests

OLLAMA_API = 'http://127.0.0.1:11434'
MODEL_NAME = 'qwen2.5:7b'
SHORT_MODEL = 'qwen2.5:3b'


def is_available(timeout=2):
    """Ollama 服务是否已启动且可用。"""
    try:
        r = requests.get(f'{OLLAMA_API}/api/tags', timeout=timeout,
                         proxies={'http': None, 'https': None})
        return r.status_code == 200
    except Exception:
        return False


def generate(system_prompt, user_prompt, timeout=120, model=None):
    """调用本地 Ollama 生成文本。

    失败抛出异常，由上层降级到知识库兜底，保证离线稳定。
    """
    model = model or MODEL_NAME
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'stream': False,
        'options': {
            'temperature': 0.7,
            # 报告已缩短，限制输出长度，避免 7B 在 6GB 显存下生成过慢
            'num_predict': 600,
        },
    }
    r = requests.post(f'{OLLAMA_API}/api/chat', json=payload,
                      timeout=timeout, proxies={'http': None, 'https': None})
    r.raise_for_status()
    return r.json()['message']['content']
