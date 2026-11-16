# Week 04

| 小节 | 主题 | 主要内容 |
| --- | --- | --- |
| [4-1](./4-1/) | Context Engineering | 区分 Prompt 与 Context，围绕登录会话修复任务讲上下文装配（直接提供 / 外部保存 / 按需补读）、优先级分配（必须项优先、工具调用与结果配对）、Token 预算（输入预算公式与溢出拦截）、压缩（工具结果结构化提取、任务交接摘要）、污染防护（过期证据、消息配对校验）与调试报告，并延伸 KV Cache 原理与 LiteLLM 网关（缓存命中、并发、限流实测）。 |
| [4-2](./4-2/) | Working Memory | 在装配器上游维护一份当前任务记录：目标、用户约束与风险、带来源与 `code_version` 的临时事实、便签（Scratchpad）中的计划与下一步；每次工具执行后按实际结果更新，保存与发送分离，并厘清工作记忆与 Agent State（消息 / 运行标记）的边界，用 JSONL 会话存储演示中断后重建输入。 |
| [4-3](./4-3/) | Memory 体系：从当前会话走向长期记忆 | 区分短期 / 长期记忆与长期记忆生命周期（筛选、记录性质与来源、检索使用、冲突处理、过期 / 废弃 / 删除）；用 OKF（Open Knowledge Format）把项目知识组织成带 YAML 元数据的 Markdown 文件，用 pi-okf / pi-okf-memory 插件在 pi 会话中建立、校验、更新与跨会话回读知识，并比较"按需检索 / 工作记忆 + Checkpoint / 完整历史"与最终采用的组合方案。 |
| [4-4](./4-4/) | RAG | 用 LlamaIndex + Milvus Lite + MiniMax/BGE 分五步跑通并检验检索增强：最小闭环（读取 → 分块 → 入库 → 检索 → 装配 → Citation → 一致性校验）、数据准备（PDF/HTML/CSV 解析保留条件与值的对应、清洗红线、六种分块对比、Overlap、节点元数据）、召回改进（向量 / BM25 / RRF 混合、元数据过滤、查询重写 / 分解 / 多路改写 / 术语扩展的实测对比）、检索后处理（LLM 与 Cross-Encoder 重排、上下文压缩、引用与实体校验）与 V0–V3 四配置统一评估。 |
| [4-5](./4-5/) | LLM Wiki：知识资产治理 | 让模型根据原始资料生成、组织和维护可复用知识页：raw/staging/wiki/manifests/logs 与 schema.md 的目录分工，草稿先检查再发布的流程，来源追踪（结论 → 知识页 → 原文）、原文更新后沿依赖传播（区分"当前批准规则 / 当前源码行为 / 历史版本"）、重复 / 过期 / 冲突的不同处理、贯穿读取-发布-使用的权限边界，以及完整性 / 可信度 / 时效性 / 可检索性四维验收。 |
| [4-6](./4-6/) | 项目四：给 Codebase Agent 装上"记忆"和"检索" | 阶段四项目：先用 OKF 给 project3 的演进版装长期记忆（`src/memory.ts` 与受治理工具 `memory_remember` / `memory_recall`，写入过审批、跨会话回读），再实现独立 RAG 后端（BGE 本地向量 + jieba BM25 + RRF 混合检索 + 带编号证据的回答）并以只读工具 `rag_query` 接入 Agent；资料与源码冲突时指出冲突、以代码为准。 |

## 4-1 目录说明

- `4-1-3-Packing.py` 至 `4-1-12-Gateway.py`：按课件小节编号的七个演示脚本——装配、优先级、预算、压缩、工具消息配对检查、调试报告与 LiteLLM 网关实测。
- `tokenizer.json`：本地 Token 计数所用的分词器配置（约 6MB）。

## 4-2 目录说明

- `4-2-2.py` 至 `4-2-6.py`：工作记忆的结构化定义、装配成输入、便签更新与 JSONL 会话存储 / 恢复的四个演示脚本。

## 4-3 目录说明

- `login_demo/`：长期记忆演示的登录示例目标仓库。
- `okf/`：课堂用 pi-okf / pi-okf-memory 生成并维护的 OKF 知识包（`index.md` 目录、`log.md` 更新历史、`sessions/` 与 `testing/` 概念文件）。

## 4-4 目录说明

- `corpus/`：登录项目语料（`docs/`、`app/` 源码、`history/`、`web/`、`tables/`、`pdf/`）。
- `rag/`：公共库——解析入库（MarkdownNodeParser + SentenceSplitter、AST 分块）、检索（向量 / BM25 / RRF）、查询改写、装配校验与指标口径；`minimax.py` 为 MiniMax embedding/LLM 适配层，`local_embed.py` 为 BGE 本地向量。
- `rag44/`：本节配套参考工程与依赖说明（`requirements.txt`、`run_questions.py` 等）。
- `scripts/`：按五步组织的 `step1`–`step5` 脚本与补充实验（Milvus 坑、Cross-Encoder 重排、技术点检查）。
- `eval/questions.json`：八道标注问题（权威出处、必备事实、禁止事实）。
- `runs/`：每次运行的标准输出记录；`milvus_login_demo.db` 为 Milvus Lite 向量库文件。
- `demo.py` / `make_pdf.py`：LlamaIndex 最小问答演示与语料 PDF 生成脚本。

## 4-5 目录说明

- `corpus/`：LLM Wiki 实验的原始语料输入（与 4-4 同源的登录项目资料）。
- `llm-wiki-demo5-step/`、`llm-wiki-demo-step9/`：`llm-wiki-demo` 两个阶段的运行快照（按 zip 原名保留），含 `raw/` 来源快照、`staging/` 草稿、`wiki/` 已发布知识页、`manifests/` 依赖清单、`reports/` 检查报告与 `schema.md` 维护规则。

## 4-6 目录说明

- `rag/`：独立 RAG 后端——`ingest.py` 入库（BGE 向量库 + BM25 索引）、`query.py` 查询（混合检索 + LLM 生成，`--json` 机器可读、`--no-llm` 纯检索仍返回完整引用），`rag/` 内含 hybrid（RRF 融合）、local_embed（bge-small-zh-v1.5）、answer（带编号证据装配）与 llm 模块；`storage/` 为已建好的索引。
- `project4/`：阶段四工程主体（project3 的演进版），新增 `src/memory.ts`、受治理工具 `memory_remember` / `memory_recall` 与只读检索工具 `tools/rag.ts`，`platform.yaml` 增加记忆与 RAG 开关，运行方式见其 README。
- `OKF长期记忆-提示词.md` / `RAG-提示词.md`：两个机制的生成规格提示词。
- 样本与运行产物口径与 3-7 相同：`samples/` 以普通目录发布（原包内嵌的 baseline `.git` 未随包发布，需 `git diff` 验证时先在样本内重建基线）；`v2/data/platform.db` 与 `.eval-runs/` 为可再生运行产物不入库，`v2/artifacts/eval_report.{md,json}` 为评估交付物。
