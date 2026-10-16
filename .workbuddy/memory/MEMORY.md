# CropGuard 项目长期记忆（.workbuddy/memory/MEMORY.md）

## 本地运行环境（2026-10-16 建立，本机）
- **可用 venv**：`D:\WorkBuddy\CropGuard\.venv`（uv, Python 3.12，torch 2.14.1+cpu / torchvision /
  fastapi / uvicorn / onnxruntime / PyQt5 / scikit-learn / timm / dill / xlwt…）。
  本机已无 `D:\Dev Env\Python`、`D:\Miniconda`（旧文档过时）。
- 起服务：`CG_DEVICE=cpu ./.venv/Scripts/python.exe api_server.py`（config 写死 cuda:0，用环境变量覆盖，不改配置）。
- 跑测试：`./.venv/Scripts/python.exe tests/run_tests.py`（当前 **145 passed, 1 skipped**；TTS 用例已修复）。
- 权重 best.pt 加载额外要 `dill`；vendored ultralytics 8.2.0 额外要 `timm`（不在 requirements 里）。

## EdgeJev 本地类型化决策（已部署）
- 模型：`edge_models/jev-int8/`（335MB，laya 后端 mmBERT-base INT8；README 里有实测校准记录）。
  构建脚本 `scripts/build_edge_model.py`（默认 hf-mirror 镜像；`HF_HUB_DISABLE_XET=1` 必须）。
- 运行包内置：`edgejev/`（上游 Apache-2.0）；适配层 `app/edge_engine.py`；部署文档 `docs/edgejev.md`。
- **定位（2026-10-16 用户明确，勿改）**：只做"在系统预设固定选项集上的选择题决策"，
  **不提供自由问答/语言性决策**；对外只暴露"决策点"（`decide_points` 目录 + `/api/edge/decision_points`），
  通用 systemone 仅为低级协议接口（开发校准用，页面不暴露）。新增决策点：先定义选项集 →
  在 scripts/edge_probes 校准达标 → 才接入链路并写明消费方与边界。
- **核心口径（勿改）**：指令理解永远"**规则优先 + EdgeJev 高置信兜底**"
  （spray≥0.62 / stop≥0.85 / group≥0.38，见 edge_engine 常量注释）；危险词拒绝 EdgeJev 无权翻案；
  安全闸门（白名单/钳制/人工确认）与模型无关。
- 网页入口：`/edge`（决策点页：① 指令解析 ② 虫情研判(实验性) ③ 决策点规划）；接口 `/api/edge/*`；
  `ask_command` 支持 `engine` 参数。
- 改问法/阈值前先跑 `scripts/edge_probes/`（校准集复现），别拍脑袋。
- 页面题面（固定选项集）由后端 decision_points() 渲染；同名选项跨题含义不同时，
  用 `serve/pages/edge.html` 里的 `OPT_GLOSS_BY_Q` 按题目覆盖。
- laya 模型零样本弱（官方模型卡自述 near-chance），只宜兜底/固定题型；升级路径：
  edgejev.train 微调 → 重新 build，接口与决策点定义不变。

## 现场测评页 /eval（Web 版评测面板）
- 页面：`serve/eval_page.py` + `templates/eval.html` + `static/eval.{css,js}`；口径 `app/eval_score.py`
  （与 GUI main.py 共用同一份纯函数，改口径只改这里）。
- 样本目录 `EVAL.sample_dir`（默认 ./pictures20；现放着 4 张 Demo 占位图，演示前替换成实拍 20 张）。
- 报告落盘 `EVAL.report_dir`（默认 ./output/eval_reports，csv 列与 GUI 导出对齐）。

## 约定与环境坑（沿用既有文档，补充新坑）
- 中文命令解析的停止词表在 `llm/ai_gateway.py`：`_STOP_WORDS`（关闭/关掉/关了/关一下…）；
  **危险词检查必须先于停止词检查**（否则"关闭安全限制"会被当成停止指令）。
- 无头浏览器验证（headless-web-verify）：本机 Edge 155 下 `--window-size` 无效，
  必须 `Emulation.setDeviceMetricsOverride`；后台常驻 Edge 要用 PowerShell「Start-Process + 存活轮询」。
- 构建/下载缓存目录（勿删）：`D:\WorkBuddy\.hf-cache`、`D:\WorkBuddy\.uv-cache`、`edgejev\.build-venv`。
