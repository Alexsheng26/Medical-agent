# 科研中间体 · Medical Research Agent

面向**临床驱动的基础与转化研究**的个人科研中间体。不是通用聊天机器人，而是围绕
**你自己的课题方向、数据基础、目标期刊和写作风格**持续沉淀的协作工具。

覆盖完整链路：文献检索 → 结构化提炼 → 假说打磨对话 → Proposal 成文 → 期刊风格内化
→ 数据评估 → 分节写作 → 母语化 → 去 AI 化。

设计取舍来自一条判断：**科学判断权始终在研究者手里**，工具负责承担劳动、守住证据边界、
并且在你想偷懒的地方拦住你。

---

## 安装

```bash
git clone https://github.com/Alexsheng26/Medical-agent.git
cd Medical-agent
pip install -e .

export ANTHROPIC_API_KEY="sk-ant-..."      # 必需（写入 ~/.bashrc 更方便）
mra init --email your@email.edu            # NCBI 要求提供联系邮箱
```

需要 Python ≥ 3.10。唯一的第三方依赖是 `anthropic`。

---

## 5 分钟上手

```bash
mra guide                                     # 中文流程速查

# 1. 建库：自动生成 PubMed 检索式 → 抓取 → 入库
mra search "NASH 门脉纤维化中巨噬细胞来源的 TGF-β1" --max 60
mra digest                                    # 逐篇结构化提炼

# 网络到不了 NCBI？在浏览器里搜好，Send to → File → Format: XML，然后：
mra import ~/Downloads/pubmed_result.xml --topic "NASH 纤维化"
# 想先试试手感，仓库里有现成的 8 篇示例语料：
mra import examples/demo_corpus.xml

# 2. 磨假说：多轮对话，每轮都基于本地文献库检索
mra chat
mra hypothesis --note "第一版"                 # 冻结为可版本比较的假说
mra proposal --references -o proposal.md      # 生成 proposal

# 3. 检查（不消耗 API）
mra refs proposal.md --list                   # 核对每一条引用是否真实存在
mra lint proposal.md                          # AI 痕迹静态检查
```

---

## 完整工作流

### 目标 1 · 文献驱动的假说生成与迭代

```bash
mra search "主题或临床问题" --max 60      # 模型先规划检索式（MeSH + 同义词 + 别名）
mra search "..." --query '肝纤维化[MeSH] AND ...'   # 或直接用你自己写好的检索式
mra digest                                # 每篇提炼：科学问题/发现/方法/创新点/局限/证据等级
mra chat                                  # 苏格拉底式追问，非总结式复述
mra hypothesis --note "加入 Kupffer 反证后"
mra hypotheses                            # 列出所有版本
mra diff 1 3                              # 逐字段比较两版假说的演化
```

对话不会替你下结论。它默认追问的是：**因果链里哪一步是假设而非实证**、**还有什么能产生
同样的观察**、**什么结果能证伪**。假说冻结时会强制列出 `challenging_pmids`（反证文献）和
`open_weaknesses`（最可能被审稿人拦住的地方）——如果反证列表是空的，它会告诉你检索做得不够对抗。

### 目标 2 · 期刊风格内化

```bash
# 仅用摘要起步（快，但只够支撑标题/摘要层面的判断）
mra journal add "Hepatology"

# 加入全文样本后才真正可用于分节写作
mra journal add "Hepatology" --samples ./samples/hepatology
mra journal list
mra journal show hepatology
```

把 3–5 篇**与你工作最接近的**该刊论文，从 PDF 复制成 `.txt` 放进目录即可。
提炼出的是可计数的规则（Introduction 几段、每段做什么、Results 如何排序、
时态与语态、claim 强度），不是"要清晰简洁"这种无用描述。

### 目标 3 · 数据评估与分节写作

```bash
mra assess data.csv --journal Hepatology --notes "n=12/组，两批独立队列"
mra draft results --journal Hepatology --data data.csv -o results.md
mra finalize results.md --journal Hepatology     # 一步产出 v1 / v2 / 报告
```

评估按五个维度打分（新颖性、机制深度、临床相关性、数据稳健性、故事紧凑度），
**3 分是专业期刊的门槛线，5 分是顶级综合刊的门槛线**。输出包含：

- 数据目前**最能站得住的那个 claim**
- **过度声称清单**——你想写但审稿时撑不住的句子
- 补实验建议，按"每单位工作量能抬高多少分"排序
- 如果匹配度不够，直接给更合适的目标期刊

### 目标 4 · 长期沉淀

```bash
mra fingerprint ./my_papers      # 从你自己发表过的论文学习文风
mra memory --refresh             # 课题方向图谱（概念频次 + 共现关系 + 假说轨迹）
mra export -o backup.json        # 全量导出，不锁定在工具里
```

方向图谱完全由本地数据确定性生成，不消耗 API。

---

## 母语化与去 AI 化

两步分开，因为它们解决的是不同问题。

```bash
mra nativize draft.md --journal Hepatology   # 母语化：搭配、冠词、语块、话题-主语语序
mra lint draft.md                            # 静态检查（离线）
mra polish draft.md                          # 由 lint 结果驱动的迭代改写
mra finalize draft.md --journal Hepatology   # v1 + v2 + 报告
```

**母语化**专门针对中文母语者的英文干扰模式：搭配错误（`make an experiment`）、
冠词与可数性（`researches` / `evidences`）、直译语块（`As we all know`、
`more and more studies`）、话题前置语序、以及 hedging 强度失准。

**去 AI 化**由静态 lint 驱动而非"请写得像人一点"——后者只会产生同义词替换。
lint 检测：固定套话、句长方差过低（burstiness）、段落长度过于均匀、
连接词开头密度、名词化密度、三项式排比、重复句首。
改写提示词里明确要求**改写句子结构，而不是替换被标记的词**。

每一轮改写后会自动比对：**引用是否丢失、数字是否消失、`[DATA NEEDED]` 标记是否被抹掉**。
任何一项异常都会在报告里告警。评分变差的改写会被丢弃并保留上一版。

---

## 三条硬约束

**1. 引用真实性（QA-1）。** 模型只能引用本地知识库里真实存在的文献，格式为 `[PMID:12345]`。
每份生成文档都会被核对：

```bash
mra refs proposal.md            # 发现伪造引用时返回退出码 1，可用于提交前的脚本卡口
```

无法支撑的论断会被标成 `[CITATION NEEDED]` 而不是编一条引用。缺引用比假引用好得多。

**2. 数据不出本地（QA-3）。** 知识库是一个 SQLite 文件，草稿是本地 Markdown。
检索用 SQLite FTS5 的 BM25，**不需要 embedding 模型、不需要向量数据库、不需要额外联网服务**。
只有你显式发起的那次调用会把内容发给模型。`.gitignore` 已排除 `.mra/`、`data/`、`samples/`。

**3. 科学责任归属（QA-2）。** 系统输出仅作辅助。投稿前必须人工通读。

---

## 命令速查

| 命令 | 作用 | 需要 API |
|---|---|:---:|
| `mra init` | 建立工作区 | |
| `mra guide` | 中文流程速查 | |
| `mra status` | 当前工作区概况 | |
| `mra search TOPIC` | 规划检索式并抓取 PubMed | ✓ |
| `mra import FILE.xml` | 导入浏览器保存的 PubMed XML（离线） | |
| `mra digest` | 逐篇结构化提炼 | ✓ |
| `mra chat [MSG]` | 科学对话（省略 MSG 进入交互） | ✓ |
| `mra hypothesis` | 冻结为带版本的假说 | ✓ |
| `mra hypotheses` / `mra diff A B` | 版本列表 / 逐字段比较 | |
| `mra proposal` | 生成 proposal | ✓ |
| `mra journal add NAME` | 建立期刊风格档案 | ✓ |
| `mra assess FILE --journal N` | 数据–期刊匹配度评估 | ✓ |
| `mra draft SECTION` | 按期刊风格写某一节 | ✓ |
| `mra nativize FILE` | 母语化改写 | ✓ |
| `mra lint FILE` | AI 痕迹静态检查 | |
| `mra polish FILE` | 迭代去 AI 化 | ✓ |
| `mra finalize FILE` | v1 + v2 + 报告 | ✓ |
| `mra refs FILE` | 引用真实性核对 | |
| `mra fingerprint DIR` | 学习你的文风 | ✓ |
| `mra memory --refresh` | 课题方向图谱 | |
| `mra export` | 全量导出 JSON | |

`import` / `lint` / `refs` / `memory` / `status` / `guide` **完全离线**，没有 API key 也能跑。

---

## 需要说清楚的三件事

原提案里有几条验收标准，软件层面做不到承诺，这里讲明白：

**"AI 检测工具得分 < 15%" 无法保证。** GPTZero、Turnitin 这类检测器是基于 token 似然的
统计分类器，闭源、会变、且不可从外部复现。`mra lint` 是一个**规则化的编辑清单**——
它检测的是让文本读起来像机器写的那些表层习惯，分数低意味着这些痕迹没有了，
**不等于对任何商业检测器的预测**。这一点在 `mra/deai.py` 的文档字符串和每次 lint 输出里都写着。

**"风格相似度 > 80%" 需要人工盲评。** 工具能提炼并复用期刊的结构与语言规则，
但相似度本身得由人判断，代码给不出这个数字。

**摘要建库有天花板。** 只用 PubMed 摘要建期刊档案，能支撑标题和摘要层面的判断；
Introduction 叙事节奏、Results 排序这类结构性结论，必须加全文样本才可靠。
`mra journal list` 会标注每个档案的来源，`journal add` 在只用摘要时会主动提醒。

**PubMed 直连需要能访问 `eutils.ncbi.nlm.nih.gov`。** 国内网络下经常不稳定。
这种情况用 `mra import`：在浏览器里正常检索，`Send to → File → Format: XML` 存下来再导入，
后续所有环节完全一样。仓库里的 `examples/demo_corpus.xml` 是一份 8 篇的示例语料，
可以直接导入试整条链路。

---

## 目录结构

```
mra/
  cli.py          命令行入口
  config.py       配置（环境变量 > 配置文件 > 默认值）
  llm.py          Claude 调用层：缓存断点、effort、拒答与 fallback 统一在这里
  pubmed.py       E-utilities 客户端 + XML 解析（纯函数，可离线测试）
  store.py        SQLite 知识库 + FTS5/BM25 检索
  retrieval.py    证据上下文组装（引用合法性的来源）
  schemas.py      结构化输出契约
  pipeline.py     检索式规划 → 抓取 → 提炼
  dialogue.py     科学对话、假说版本化、proposal 生成
  journal.py      期刊风格建模
  assess.py       数据–期刊匹配度评估
  writing.py      分节写作、母语化、去 AI 化迭代循环
  deai.py         AI 痕迹静态检测（确定性，离线）
  citations.py    引用真实性核验
  memory.py       方向图谱 + 写作指纹
  prompts/*.md    所有提示词，Markdown 明文，可直接改
examples/         示例语料（可直接 mra import）
tests/            136 个测试，全部离线运行
docs/PROPOSAL.md  条款式 + 提纲式方案书
```

**提示词全部是 `mra/prompts/` 下的 Markdown 明文。** 觉得追问太温和、
评估太宽松、去 AI 化改得太多——直接改文件，不用碰 Python。这是有意的设计。

---

## 开发

```bash
pip install -e ".[dev]"
python -m pytest -q          # 136 passed，不需要 API key 和网络
```

测试覆盖：PubMed XML 解析、FTS5 检索与排序、假说版本化、AI 痕迹评分与句子切分、
引用核验、写作保真守卫、方向图谱、以及用 mock transport 校验的 **API 请求线格式**
（模型 id、adaptive thinking、effort、缓存断点、fallback 降级、拒答处理）。
