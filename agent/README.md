# qiansai

全国大学生嵌入式芯片与系统设计竞赛 2026，AMD 3.1 的 RTL 方向。
以 ChipMATE-V-4B 与 ChipMATE-P-4B 为首个模型方案，先在 NVIDIA 上复现，
再迁移到 AMD。采用独立比赛工程，固定版本保留上游代码。

**当前状态（2026-10-18，测试仍在继续）：双 4B 已完成 AMD/Vivado 官方脚本的
156 题配套对照。原方案 116 L3 / 32 L1 / 8 L0，原样基线 75 L3 / 40 L1 / 41 L0；
官方能力分 23.54/30、增益分 35.98/40。每题一次，成本与工程分未测。**
显存每秒采样峰值 29.862 GB，开发卡实际为 48 GiB；绝对 14 GB/model 已通过真实冷启动；物理
32 GB/R9700 仍未实测。25 GiB 持久盘已保存全部约 18.20 GB 模型文件，原传输包
留在临时盘。用户授权的 10 小时自主迭代截至北京时间 10:00:58。

兼容性、逐题结果、策略退步与后续新生成实验见
[自主迭代报告](docs/guides/autonomous_iteration_20261018.md)和
[完整会话证据](docs/journal/20261017_234742_autonomous_iteration.md)。
本地最近一次完整 smoke：94 项 pytest 通过。自有 Docker 镜像尚未构建验收。
旧 NVIDIA/Icarus 156 题的 109 PASS / 47 FAIL 和五项 CPU 参考异常仍保留，
它与当前 AMD/Vivado 等级评测口径不同，不能直接视为算法提升。

2026-10-17 [服务器说明复核](docs/journal/20261017_211323_server_usage_review.md)：官方已提供
Radeon Cloud 上手 notebook、授权与持久化配置说明；单题时限现为 900 秒，基线输出上限
16384 token，提交允许 Gitee/GitHub 私有仓库二选一。辅助模型规则已发布，但 P-4B 生成
Python 是否符合“不直接生成代码”的分类仍待明确；下方 10-16 记录中的旧参数以本次为准。

2026-10-16 [最新赛题复核](docs/journal/20261016_122513_rules_review.md)：当前原版不能直接
提交，需适配官方原样基线与异步投题协议；正式输入只有题面。最终 Vivado 评分由赛事方
执行，内部自验证是否可自带 Icarus 仍待明确，本地 Vivado 自测用于检验兼容性。
已有 NVIDIA/Icarus 结果保留为开发证据。代码封存为 10 月 31 日 10:00（北京时间）。

## 文档入口

- [Radeon Cloud 官方 Vivado 配置与已验收结果](docs/guides/radeon_vivado.md)

2026-09-15：新增单模型独立评分、逐题 CSV、请求 token 统计与显存采样入口，
当前本地 29 项 pytest 通过。AMD 实例已完成环境检查与小型 BF16 运算，
SSH 仍间歇断开，权重需服务器 HF 登录；模型部署及三组测评尚未完成，
详见 [本次会话](docs/journal/20260915_142939_amd_resume.md)。

- [上下文与术语](docs/context.md)
- [当前共识与阶段目标](docs/decisions.md)
- [完整架构](docs/architecture.md)
- [比赛规则与 109/156 结果解读](docs/competition.md)
- [本地文件与新服务器部署](docs/guides/server_setup.md)
- [逐题评分、token 与显存记录](docs/guides/benchmark_records.md)
- [逐次工作日志](docs/journal/)
- [协作规则](AGENTS.md)
- [原始赛题指南](docs/references/amd_2026_guide.pdf)

## 获取仓库

```bash
git clone --recurse-submodules https://github.com/fusang991/qiansai.git
cd qiansai
# 已经 clone 的仓库：
git submodule update --init --recursive
```

上游固定版本：

| 内容 | 来源 | 提交 |
| --- | --- | --- |
| ChipMATE | https://github.com/zhongkaiyu/ChipMATE | d94087b5159aea482648b26398a2ffb164246671 |
| VerilogEval v2 | https://github.com/NVlabs/verilog-eval | c498220d0a52248f8e3fdffe279075215bde2da6 |

官方 dataset_spec-to-rtl 包含 156 道题，每题有题目、参考实现和测试台。
上游各自的许可保留在子模块中；本仓库尚未另行选择自有代码的开源许可证。

## 本地准备与检查

Python 3.11 或更高版本可执行数据准备和骨架检查；推荐部署阶段使用 Python 3.12。
uv.lock 固定本工程的轻量 Python 依赖；GPU 环境使用独立的 CUDA 依赖锁，镜像版本另行固定和验证。

```bash
uv sync --frozen
python3 scripts/prepare_data.py
bash scripts/smoke_test.sh
```

数据转换结果在 data/processed/，只向生成流程提供题目和接口声明。
参考实现正文和官方测试台保留在 data/verilog_eval/，用于独立评测。

## AMD/Vivado 服务器入口

当前部署包提供官方目录协议、原样裸跑基线和逐题独立 Vivado 判定：

```bash
bash /workspace/qiansai-ready/run_selftest.sh
bash /workspace/qiansai-ready/run_selftest.sh --case ex01_popcount8
bash /workspace/qiansai-ready/run_selftest.sh --tasks /workspace/qiansai-iterations/20261017_234742/tasks_veval --case Prob046_dff8p
```

每次生成唯一结果目录并打印每题 L 等级、失配数和官方子集分数；默认每题一次。
重复采样可加 `--samples 5`。实际验收记录及结果见上述自主迭代报告。

## NVIDIA/Icarus 历史入口

服务器完成依赖和权重准备后：

```bash
./run.sh --case Prob001_zero
./run.sh --case Prob007_wire --case Prob034_dff8
./run.sh --suite verilogeval-v2
./run.sh --suite verilogeval-v2 --limit 3
./run.sh --list-cases
```

入口会检查两份服务，必要时从本地权重启动它们，调用固定版本 ChipMATE，
再用官方参考实现和测试台进行独立 Icarus 评测。终端分别显示编译、仿真和最终结果，
以及 RTL 和 metrics.json 的路径。退出码：0 全部通过，1 存在功能/编译失败，
2 环境、生成或其他执行错误。环境失败不会标记为模型通过。

每次结果写入唯一 runs/ 目录；用 --run-dir 指定目录时拒绝覆盖已有目录。
保存题目与接口输入、配置、代码版本与文件哈希、每批提示词和模型返回文本、
候选 RTL、Python 实现、官方评测原始日志和结果。自验证一致率与独立结果分开。
单次开发运行不宣称复现论文的 pass@1/pass@5。

2026-09-11 全量实测使用提交 0870769 的干净工作区，14:08 开始、17:08 完成，
总耗时 10825 秒（约 3 小时），双服务启动合计 116.23 秒；单题耗时中位数 38.85 秒。
同卡显存每秒采样最大值 30201 MiB（29.49 GiB），命令结束后恢复到 1 MiB。
原始 47 个 FAIL 包含 35 个输出不匹配、10 个编译失败、2 个官方测试台超时。
参考实现对照中的 5 个异常涉及接口不一致、Icarus 11 的参考语法兼容性和测试台时限；
不能将原始 47 个 FAIL 全部直接归因于模型。详见上述全量记录。

默认在命令结束时回收本次启动的模型服务。连续开发可先在另一终端运行
bash scripts/serve_models.sh；随后 run.sh 会复用已有服务。--no-start 要求服务预先就绪。

## NVIDIA 环境准备

下载和安装直接在目标服务器执行。云平台本身提供容器、没有 Docker 服务时，
使用两个独立 Python 环境：.venv 放工作流，.venv-vllm 放 GPU 推理依赖。
scripts/setup_cuda.sh 使用 docker/requirements.cuda.lock 的完整版本与哈希，要求 Python 3.12、
uv、Icarus/vvp 和可用的 NVIDIA 驱动；不会安装或修改宿主机驱动。

```bash
bash scripts/setup_cuda.sh
# 先在两个模型页面获得访问权限，再在服务器交互登录：
.venv/bin/hf auth login
.venv/bin/python scripts/fetch_models.py
./run.sh --case Prob001_zero
```

两份权重在 model/manifest.json 中固定 revision；下载后记录文件 SHA256。
MODEL_ROOT 可以指定本地权重目录；QIANSAI_VLLM_EXECUTABLE 可以指定 vLLM 可执行文件。
凭据只放 HF 标准登录缓存或运行环境，不写进仓库。长任务在服务器用 nohup/tmux
执行，单独保留日志和退出状态。权重、下载缓存和实验产物需要独立备份。

## Docker 入口

Docker 文件是待实机验证的部署模板。先选择支持两份权重的 vLLM CUDA 镜像，
固定到标签或 digest，取得 Hugging Face 权重访问权限，按 model/manifest.json
下载固定 revision。部署机器需要能同时容纳两个模型及其运行缓存。

```bash
cp .env.example .env
# 编辑 .env：CUDA_VLLM_IMAGE、GPU_DEVICE_ID、MODEL_ROOT 等。
# 下载 manifest 中固定版本的两份模型：
uv run python scripts/fetch_models.py
docker compose --env-file .env -f docker/compose.cuda.yaml build
docker compose --env-file .env -f docker/compose.cuda.yaml up -d
```

容器内有两个本地 vLLM 服务和 ChipMATE/Icarus 工作流；模型、数据和结果通过挂载持久保存。
这条流程已在云平台现有容器中完成双模型生成、自验证、独立功能评测与原始输出落盘；
上面的自有 Docker 镜像命令仍待具备 Docker 的环境验证。

已经启动服务时，也可以在宿主机直接运行：

```bash
bash run.sh --input data/processed/verilogeval_v2.jsonl --out runs/manual/agent.jsonl --limit 1
bash run_baseline.sh --input data/processed/verilogeval_v2.jsonl --out runs/manual/baseline.jsonl --limit 1
```

以上 JSONL 入口保留上游原始输出接口，不自动进行独立评分；日常开发使用 --case/--suite。
这些均为开发入口，尚未公布的官方提交协议及 Vivado 接口在后续阶段接入。

## 记录与存储

docs/journal/ 每次新增一份记录，三份核心文档维护当前共识。每次运行使用独立 runs/
目录，保留原始输入输出、配置、版本和日志。权重、大型运行产物、缓存和本机 .env
不进入普通 Git；其身份与需要分享的结果进入模型清单和工作日志。
被 Git 忽略不等于已经备份，迁移实验历史时还需同步相应 runs/ 和权重存储。
