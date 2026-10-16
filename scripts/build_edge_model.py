# -*- coding: utf-8 -*-
"""一键构建 EdgeJev 本地决策模型（把模型部署到 CropGuard）。

用法（在仓库根目录执行）：

    python scripts/build_edge_model.py                     # 默认：laya 后端 + int8（~335MB，推荐）
    python scripts/build_edge_model.py --precision fp32    # 逐位一致（~1.29GB，慢一些）
    python scripts/build_edge_model.py --backend kev --precision fp32   # 换后端（Qwen2.5-0.5B）
    python scripts/build_edge_model.py --mirror https://hf-mirror.com   # 手动指定下载镜像

脚本做四件事（只有第 1、2 步需要联网，且只需做一次）：
  1) 建构建环境 .edge-build-venv/ 并安装依赖（torch/transformers/laya/onnx/onnxscript…）
  2) 从 HuggingFace 拉上游权重（默认走高可用镜像 hf-mirror.com，国内直连 HF 常被墙）
  3) 调用 `edgejev build` 导出 ONNX -> 量化 -> 多形状自检
  4) 用产出的模型跑一条**中文类型化决策**做终验（dept 路由示例）

产物是一个自包含目录（model.onnx + tokenizer.json + edgejev.json），
拷到任何一台内网机器即可离线使用；运行时只需要 onnxruntime + tokenizers + numpy，不需要 torch。
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_DIR = os.path.join(ROOT, '.edge-build-venv')
DEFAULT_OUT = os.path.join(ROOT, 'edge_models', 'jev-int8')
BACKEND_DEFAULT_MODEL = {
    'laya': 'convaiinnovations/laya-multilingual',
    'kev': 'jaredpalmer/kev-0.5b',
    'nanojev': 'C-Tianyu/NanoJev',
}

BUILD_DEPS = ['torch', 'transformers>=4.48,<5', 'laya>=0.3.0', 'safetensors', 'onnx',
              'onnxscript', 'onnxconverter-common', 'peft', 'onnxruntime', 'numpy',
              'huggingface_hub']


def _run(cmd, env=None, cwd=ROOT):
    print('  $', ' '.join(str(c) for c in cmd), flush=True)
    proc = subprocess.run(cmd, cwd=cwd, env=env)
    if proc.returncode != 0:
        sys.exit(f'命令失败（退出码 {proc.returncode}）：{" ".join(str(c) for c in cmd)}')


def _venv_python():
    for rel in ('Scripts/python.exe', 'bin/python'):
        p = os.path.join(VENV_DIR, rel)
        if os.path.exists(p):
            return p
    return None


def _build_env(mirror):
    env = dict(os.environ)
    if mirror:
        env['HF_ENDPOINT'] = mirror
    # hf-mirror 不支持 Xet 存储协议；不关掉会报 LocalEntryNotFoundError
    env.setdefault('HF_HUB_DISABLE_XET', '1')
    env.setdefault('HF_HUB_ETAG_TIMEOUT', '30')
    env.setdefault('HF_HUB_DOWNLOAD_TIMEOUT', '120')
    env.setdefault('NO_PROXY', 'localhost,127.0.0.1,::1')
    return env


def ensure_venv():
    """建构建环境（uv 优先 —— 快得多；没有 uv 就退回标准 venv + pip）。"""
    if _venv_python():
        print('[1/4] 构建环境已存在：%s' % VENV_DIR)
        return _venv_python()
    uv = shutil.which('uv')
    if uv:
        print('[1/4] 用 uv 创建构建环境：%s' % VENV_DIR)
        _run([uv, 'venv', '--python', '>=3.10', VENV_DIR])
    else:
        print('[1/4] 用 python -m venv 创建构建环境：%s' % VENV_DIR)
        _run([sys.executable, '-m', 'venv', VENV_DIR])
    return _venv_python()


def install_deps(py):
    print('[2/4] 安装构建依赖（首次约 1-2GB，之后走缓存很快）…')
    uv = shutil.which('uv')
    if uv:
        _run([uv, 'pip', 'install', '--python', py] + BUILD_DEPS)
    else:
        _run([py, '-m', 'pip', 'install', '--upgrade', 'pip'])
        _run([py, '-m', 'pip', 'install'] + BUILD_DEPS)


def build_model(py, args, env):
    print('[3/4] 导出 + 量化（backend=%s precision=%s）…' % (args.backend, args.precision))
    code = (
        'import os, sys\n'
        'sys.path.insert(0, os.getcwd())\n'
        'from edgejev import build\n'
        'build.run(backend=%r, model=%r, subfolder=None, out=%r, precision=%r, keep_fp32=False)\n'
        % (args.backend, args.model, args.out, args.precision)
    )
    _run([py, '-c', code], env=env)


_VERIFY_CODE = '''
import os, sys, time
sys.path.insert(0, os.getcwd())
from edgejev import Agent

ag = Agent(__MODEL__)
state = "我的信用卡被扣了两次款，麻烦退一笔。"
qs = {"dept": {"type": "choice", "instructions": "该转给哪个组？",
               "criteria": {"billing": "支付、扣款、退款", "technical": "报错", "sales": "定价"}},
      "urgent": {"type": "noul", "instructions": "这条消息表达了紧急或时间压力"}}
t0 = time.time()
r = ag.system_one(state, qs)
ms = (time.time() - t0) * 1000
dept = r["answers"]["dept"]
print("  dept =", dept["choice"], dept["probabilities"])
print("  单次耗时 {:.1f} ms | backend={} precision={}".format(
    ms, ag.cfg.get("backend"), ag.cfg.get("precision")))
if dept["choice"] == "billing":
    print("  [OK] 模型可用（官方示例路由命中）")
else:
    print("  [注意] 官方示例未命中 billing ->", dept["choice"],
          "（不影响部署，但建议人工核对模型质量）")
'''


def verify(py, args, env):
    print('[4/4] 终验：用产出的模型跑一条中文类型化决策…')
    code = _VERIFY_CODE.replace('__MODEL__', repr(args.out))
    _run([py, '-c', code], env=env)


def main():
    ap = argparse.ArgumentParser(description='构建 EdgeJev 本地决策模型并部署到 CropGuard')
    ap.add_argument('--backend', default='laya', choices=sorted(BACKEND_DEFAULT_MODEL))
    ap.add_argument('--model', default=None, help='HF 模型 id 或本地路径（默认按后端选）')
    ap.add_argument('--precision', default='int8',
                    help='int8（默认/推荐）| int8-pc | mixed | fp32')
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--mirror', default='https://hf-mirror.com',
                    help='HF 镜像（默认 hf-mirror.com；传空字符串则直连 huggingface.co）')
    args = ap.parse_args()
    args.model = args.model or BACKEND_DEFAULT_MODEL[args.backend]

    print('EdgeJev 模型构建 —— 后端 %s / 精度 %s' % (args.backend, args.precision))
    print('  输出目录：%s' % args.out)
    print('  权重来源：%s（镜像 %s）' % (args.model, args.mirror or '直连'))

    py = ensure_venv()
    install_deps(py)
    env = _build_env(args.mirror)
    build_model(py, args, env)
    verify(py, args, env)

    size_mb = 0
    for name in os.listdir(args.out):
        size_mb += os.path.getsize(os.path.join(args.out, name))
    print('\n完成 -> %s（%.0f MB，自包含可拷贝）' % (args.out, size_mb / 1e6))
    print('  启动服务后访问 http://127.0.0.1:8080/edge 查看推理入口；')
    print('  GET /api/edge/status 可查引擎状态。配置项见 config/configs.yaml 的 EDGE 段。')


if __name__ == '__main__':
    main()
