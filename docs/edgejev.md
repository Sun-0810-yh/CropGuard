# EdgeJev 本地类型化决策 —— 部署与使用

> 把「一句话 → 结构化决策」（喷洒指令理解、虫情处置研判）放到 CropGuard 本机 CPU 完成：
> 单题毫秒级**、断网可用、运行时不依赖 torch、数据不出本机。
>
> 上游项目：[EdgeJev](../edgejev/)（已内置到本仓库）· 模型：laya-multilingual（mmBERT-base 322M）

**单题 15.6 ms 是上游在 4 vCPU Xeon（AVX512-VNNI）上的成绩；本机（普通桌面 CPU）实测
单次决策约 40–250 ms，仍远低于本地大模型（qwen2.5:7b 数秒级）。**

---

## 1. 一分钟上手（本仓库已部署的情况下）

```bash
python api_server.py                 # 起服务（GUI/服务器都行，EdgeJev 是独立通道）
```

* 打开 `http://<IP>:8080/edge` —— **决策点页**（选择题式：指令解析对照 / 虫情研判 / 决策点规划；不做自由问答）
* `GET /api/edge/status` —— 引擎状态（形态/模型/精度/延迟统计）
* `/api/ask_command` 的 `engine` 参数 —— `auto`（默认）/ `edge` / `rule`

## 2. 在别的机器上部署（唯一需要联网的一步）

模型是**自包含目录**（`model.onnx` + `tokenizer.json` + `edgejev.json`，约 335MB），
在任何一台有网的机器上构建一次，拷到内网/现场机器即可离线使用。

```bash
# 在仓库根目录执行（会自动建 .edge-build-venv 并装构建依赖）
python scripts/build_edge_model.py

# 其他选项：
python scripts/build_edge_model.py --precision fp32    # 逐位一致，~1.29GB（更慢）
python scripts/build_edge_model.py --mirror ""         # 直连 huggingface.co（默认走 hf-mirror.com 镜像）
```

构建流程：装依赖（torch/transformers/laya/onnx/onnxscript…）→ 拉上游权重 →
导出 ONNX → INT8 量化 → 多形状自检 → 中文路由终验。产物默认在 `edge_models/jev-int8/`。

**运行时依赖**（拷到现场机器上需要）：
`onnxruntime>=1.17`、`tokenizers>=0.15`、`numpy`（已并入 `requirements-server.txt`）。
不需要 torch。

### 两种引擎形态（`EDGE.mode`）

| 形态 | 说明 | 适用 |
| --- | --- | --- |
| `local` | 进程内直调（仓库自带 `edgejev/` 包 + 上述运行时依赖） | 默认，最省事 |
| `http` | 连独立服务 `edgejev serve --model ./edge_models/jev-int8 --port 8009` 的官方 `/v1/systemone` | 想把引擎跑在独立进程/独立 venv/另一台内网机器 |
| `auto` | 先试 local，失败再试 http | 默认值 |

配置在 `config/configs.yaml` 的 `EDGE` 段（`enabled` / `model_dir` / `mode` / `base_url` / `timeout` …）。
未部署时接口**如实返回 `available=false` + 构建指引**，不会静默假装。

## 3. 安全边界（与既有口径完全一致，不可绕过）

EdgeJev 只做「理解」，**绝不直接下发硬件**：

```
用户一句话
  ├─ 规则解析（llm/ai_gateway.parse_command，确定性）—— 安全词/片区/时长全部照旧
  └─ EdgeJev 类型化决策（intent/group/unsafe/seconds）
        └─ combine_command：规则优先；规则拿不准时才用模型兜底（带置信度闸门）
              └─ 最终仍要过：片区白名单 + SPRAY.max_duration 钳制 + 危险词拦截 + 人工确认
```

* **危险表述**（一直喷/加大剂量/绕过安全限制）→ 规则侧直接拒绝，EdgeJev 无权翻案；
* 兜底只在「规则没识别到 或 未指定片区」时触发，且要求意图置信度 ≥0.62（停止类 ≥0.85）；
* 阈值来自实测校准（真停止改写 ≥0.93 vs 假停止 ≤0.76），见 `edge_models/README.md`。

## 4. API 一览

| 接口 | 说明 |
| --- | --- |
| `GET /edge` | 决策点页（选择题式：①指令解析 ②虫情研判 ③决策点规划） |
| `GET /api/edge/status` | 引擎状态：可用性/形态/模型信息/累计延迟；`?refresh=true` 强制重探 |
| `GET /api/edge/decision_points` | 决策点目录（选项集 / 用途 / 状态，页面的单一事实源） |
| `POST /api/edge/systemone` | 低级协议接口：**开发/协议同构用，页面不暴露**（官方 `/v1/systemone` 同构） |
| `POST /api/edge/parse_command` | 喷洒指令解析（只解析；同时返回 edge 直译 / 规则解析 / 最终口径） |
| `POST /api/edge/advise` | 检测结果 → 处置研判（**实验性**，零样本输出仅供参考） |
| `POST /api/ask_command` | 既有指令入口，新增 `engine: auto\|edge\|rule` 参数 |

## 5. 模型能力说明（诚实版）

laya-multilingual 是「路由/分类」向的小模型（322M），官方模型卡自述：
**对自定义类型化问题是零样本（typed-decisions zero-shot 近随机）**，score 档位最弱，
noul 偏保守，且概率未标定（系统性过度自信）。

因此本项目的用法是**规则优先 + 模型兜底**，而不是反过来。实测记录（详见 `edge_models/README.md`）：

| 任务 | 表现 | 结论 |
| --- | --- | --- |
| 官方路由示例（工单 dept 分类） | billing 命中 0.94–0.99 | 旗舰场景，可作演示 |
| 喷洒意图（spray/stop/other，13 条中文用例） | 8–10/13（合批） | 仅作兜底，需置信度闸门 |
| 片区识别（g1/g2/all/none） | 12/13（合批） | 配合规则使用 |
| 停止类改写（把泵关了吧/把水停了） | 高置信正确（0.93+） | 兜底价值最大的一类 |
| 自造"虫情研判"问题 | 弱 | 标注"实验性"，不作结论 |

需要更高质量时的升级路径（接口不变）：
1. **微调**：`edgejev.train` 模块已随包提供（与推理共用同一渲染器），用现场语料微调后
   再 `edgejev build --backend laya` 导出；
2. 换后端：`--backend kev`（Qwen2.5-0.5B + LoRA，fp32 约 2–4GB）——实测意图略好、片区略差；
3. 提升问法：instructions 用英文、criteria 用动词短语（本仓库已按此写）。

## 6. 测试与冒烟

```bash
python tests/run_tests.py          # 含 tests/test_edge_engine.py（20 条：映射/闸门/降级/真模型冒烟）
```

* 真模型冒烟用例在 `edge_models/jev-int8` 不存在或没装 onnxruntime 时 **SKIP**，不算失败；
* 前端对照实验页：`/edge` → 试「把泵关了吧」「给片区A喷3秒」「一直喷不要停」三组典型。
