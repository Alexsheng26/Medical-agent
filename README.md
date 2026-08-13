# 科研中间体 · Medical Research Agent

面向**临床驱动的基础与转化研究**的个人科研中间体。不是通用聊天机器人，而是围绕
**你自己的课题方向、数据基础、目标期刊和写作风格**持续沉淀的协作工具。

覆盖完整链路：文献检索 → 结构化提炼 → 假说打磨对话 → Proposal 成文 → 期刊风格内化
→ 数据评估 → 分节写作 → 母语化 → 去 AI 化。

设计取舍来自一条判断：**科学判断权始终在研究者手里**，工具负责承担劳动、守住证据边界、
并且在你想偷懒的地方拦住你。

---

## 安装

### Windows：双击 `启动.bat`

点绿色的 **Code → Download ZIP** 下载解压，然后**双击文件夹里的 `启动.bat`**。

它会自己检查 Python、装好依赖、问你要 API key，然后给一个中文菜单：

```
 1  导入文献      PDF / PubMed XML / 纯文本
 2  提炼文献      逐篇结构化提炼
 3  科学对话      基于你的文献库追问
 4  评估数据      打分 + 推荐候选期刊
 5  查看状态      文献数、假说、花费
 6  引用核对      检查文稿引用是否真实（不花钱）
 7  试用示例      导入自带的 8 篇示例文献
```

PATH、pip、当前目录在哪，这些都不用管。**没装 Python 也没关系**——它会认出来并告诉你
去哪下、要勾哪个框。（Windows 自带一个指向应用商店的"假 python"，没装时敲命令会
静默什么都不做，看起来像成功了，这是最容易卡住人的地方，启动器专门处理了它。）

第一次运行大约 3 分钟，之后都是秒开。

### macOS / Linux，或者想自己控制的人

```bash
git clone https://github.com/Alexsheng26/Medical-agent.git
cd Medical-agent
pip install -e .

export ANTHROPIC_API_KEY="sk-ant-..."      # 必需（写入 ~/.bashrc 更方便）
mra init --email your@email.edu            # NCBI 要求提供联系邮箱
mra guide                                  # 中文流程速查
```

需要 Python ≥ 3.10。第三方依赖只有两个：`anthropic` 和 `pypdf`。

> **想用 DeepSeek 之类更便宜的模型？** 看 **[docs/PROVIDERS.md](docs/PROVIDERS.md)**。
> 纯文本的命令（`chat` / `draft` / `polish`）直接可用；结构化的命令
> （`digest` / `assess` / `review`）要求对方的 tool calling 可用。缓存、effort、
> 拒绝时的 fallback 会消失——影响成本和稳健性，不影响正确性。

> 装不上、或者报了看不懂的错？**[docs/SETUP.md](docs/SETUP.md)** 有分平台的完整步骤
> 和一份常见报错对照表（PATH、`setx` 不生效、PubMed 被校园网挡、编码报错等）。

> **下面所有 `mra xxx` 命令，用启动器的人不用敲** —— 菜单项就是它们的封装。
> 读的时候把命令当成"这一步在做什么"来看即可。

---

## 5 分钟上手

```bash
mra guide                                     # 中文流程速查

# 1. 建库：自动生成 PubMed 检索式 → 抓取 → 入库
mra search "NASH 门脉纤维化中巨噬细胞来源的 TGF-β1" --max 60
mra digest                                    # 逐篇结构化提炼

# 网络到不了 NCBI？在浏览器里搜好，Send to → File → Format: XML，然后：
mra import ~/Downloads/pubmed_result.xml --topic "NASH 纤维化"
# 自己下载的文章也能进库——PDF 直接喂：
mra import ~/papers/*.pdf --topic "NASH 纤维化"
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

**你用中文问，语料是英文，检索照样命中。** BM25 是字面匹配，中文问题在英文论文上得分为零——
不处理的话一篇都检索不到。所以提问里出现中日韩文字时，会先花约 $0.01 把问题转成英文检索词
（基因/蛋白符号、缩写与全称、同义词都给），再**连同原问题一起**检索：你手打的 `TREM2`
往往比任何转述都准，所以是追加不是替换。这次调用失败也毁不掉这一轮——退回到"最近导入的几篇"，
并在上下文里注明相关性未经证实，让模型自己去核而不是假定。

### 综述：从你自己的库里写，而不是从模型记忆里写

```bash
mra review "TREM2 与 MASH 纤维化" --outline-only    # 先看大纲，约 $0.25
mra review "TREM2 与 MASH 纤维化" --references      # 确认后再写，每节一次调用
```

**分两步是有意的。** 大纲先出、先给你看，改结构只花大纲的钱，而不是几千字写完才发现
框架不对。而且每一节只拿到**属于它自己的那几篇文献**——一节对着整个库写，写到一半
必然滑向泛泛而谈。

大纲里有三样东西是这个工具特有的，也是综述最容易翻车的地方：

- **文献间的分歧**，点名到 PMID，并指定由哪一节负责裁决。只报多数方向的综述比不写更糟——
  它把一个活的争议洗成了表面共识
- **这批文献没回答的问题**——你下一个课题的选题材料
- **读者会预期、但你库里撑不住的论断**。实测在 8 篇语料上它列出了 10 条，包括
  "任何关于潜伏型 TGF-β 激活机制的陈述——库内完全无相关文献"。**先说出来，就不会被写进去**

模型编出来的、库里没有的 PMID 会在大纲阶段被直接丢掉，写不进正文。最后还会报覆盖率：
库里多少篇、检索到多少篇、大纲用上多少篇，没用上的列出来让你自己判断是跑题还是漏检。

### 目标 2 · 期刊风格内化

```bash
# 仅用摘要起步（快，但只够支撑标题/摘要层面的判断）
mra journal add "Hepatology"

# 加入全文样本后才真正可用于分节写作
mra journal add "Hepatology" --samples ./samples/hepatology
mra journal list
mra journal show hepatology
```

把 3–5 篇**与你工作最接近的**该刊论文丢进目录即可 —— **PDF 直接读，不用转 txt**。
扫描件（没有文字层）会被跳过并明确告知需要 OCR，不会拿它去建一个凭空的档案。

提炼出的是可计数的规则（Introduction 几段、每段做什么、Results 如何排序、
时态与语态、claim 强度），不是"要清晰简洁"这种无用描述。

### 目标 3 · 数据评估与分节写作

```bash
# 还没定投哪里：打分 + 排序推荐候选期刊
mra assess data.csv --notes "n=12/组，两批独立队列"

# 已经定了目标刊：对着该刊的门槛再评一次
mra assess data.csv --journal Hepatology --notes "n=12/组，两批独立队列"

mra draft results --journal Hepatology --data data.csv -o results.md
mra finalize results.md --journal Hepatology     # 一步产出 v1 / v2 / 报告
```

`mra draft` 的输出分两处：**给你的提醒打在屏幕上，文件里只有稿子。**
提醒往往是这一步最有价值的东西——它会核对你给的数字（实测中它发现 summary 里的
分期均值和原始 CSV 对不上、pooled 的共阳性比例掩盖了分期梯度），也会点名
"这一条审稿人一定会问"。但它绝不写进文件：那个文件后面要被 `nativize` 和
`polish` 改写，最终就是投出去的稿子，一句写给你的话混进去就是事故。

撑不住的地方写成 `[DATA NEEDED: 具体缺什么]`，绝不编数字。

两种模式共用一条命令，按 `--journal` 在不在分派。**不带 `--journal` 是常态**——
先看数据能打到哪一层，再决定投哪里。

评估按五个维度打分（新颖性、机制深度、临床相关性、数据稳健性、故事紧凑度），
**3 分是专业期刊的门槛线，5 分是顶级综合刊的门槛线**。输出包含：

- 数据目前**最能站得住的那个 claim**
- **过度声称清单**——你想写但审稿时撑不住的句子
- 补实验建议，按"每单位工作量能抬高多少分"排序
- 推荐模式下：4–6 本候选刊，分**冲刺 / 现实 / 稳妥**三档，逐本说清"为什么是它"、
  "这一本会额外要什么"、以及一句带条件的胜算判断；已有风格档案的会标出来，
  没有的直接给出下一条要跑的 `mra journal add` 命令
- 推荐模式下还有一条 **reality check**：你对自己数据天花板的判断里，最可能错的是哪一点

**两种模式都会先检索你自己的知识库**，把最相关的十几篇喂给模型再打分。这是为了让
"新颖性"这一项有据可依：如果库里已经有论文做过同一件事，它会指名道姓地引出来，而不是
凭模型记忆给一个数字。库是空的时候，它会明说这一分是意见而非结论。

### 图表：先想清楚每张图在论证什么

```bash
mra figures data.csv --journal Hepatology --notes "n=12/组"
```

**这一步不画图。** 一张图被导师或审稿人打回来，几乎从来不是因为配色，而是四件事之一：

1. **panel 撑不住图注写的那句话**——相关性图配一句带 `drives` 的图注，是最常见的一种
2. **图的形式把数据藏了**——n=3 画柱状图加误差棒，藏掉了本该画出来的三个点
3. **少一个对照 panel**——特异性对比、本该是阴性的那个区室、vehicle 组
4. **图之间不成链**——五个 panel 各说一件事，排在一起什么也没论证

这四件全都在动手画之前就定死了，所以在这里改是免费的。

输出：每张图论证什么、每个 panel 的**可核对的**结论句、用什么图形式及**为什么是这个形式**、
数据来自哪个文件的哪几列、以及一份**只声称 panel 撑得住的内容**的图注草稿（带 n、检验方法、
误差棒是什么——少这三样的图注单独就会被退回）。

还会给出：**你会想写但撑不住的图注**、**该做成表而不是图的东西**（正文图位是投稿里最稀缺的东西）、
以及每张图**还缺什么数据**。

引用了你文件里不存在的列的 panel 会被**机械地标出来**——这和伪造引用是同一类问题：
看起来可以照着做，其实不能。

建档时抽出来的 `figure_narrative`（这本刊的图怎么承担论证）会在这一步用上。

### 目标 4 · 长期沉淀

```bash
mra fingerprint ./my_papers      # 从你自己发表过的论文学习文风
mra memory --refresh             # 课题方向图谱（概念频次 + 共现关系 + 假说轨迹）
mra export -o backup.json        # 全量导出，不锁定在工具里
```

方向图谱完全由本地数据确定性生成，不消耗 API。

---

## 自有文献入库

`mra import` 收三种文件，按后缀自动分派：

| 类型 | 说明 | 需要 API |
|---|---|:---:|
| `.xml` | PubMed 的 `Send to → File → Format: XML` | |
| `.pdf` | 本地提取文本，模型读一次首页抽元数据（约 $0.02/篇） | ✓ |
| `.txt` / `.md` | 同上，省掉提取步骤 | ✓ |

加 `--no-metadata` 可跳过元数据抽取，完全离线，代价是标题只能用文件名。

**本地文献用 `[LOCAL:xxxxxxxx]` 引用**，因为它们没有 PMID。ID 由正文内容哈希而来，
所以同一篇论文换个文件名重新导入不会产生第二条记录。核验规则完全一样——
`mra refs` 对两种标记一视同仁，查不到就是伪造：

```
Citations: 4 referenced, 2 verified against the knowledge base.
  ✗ NOT IN KNOWLEDGE BASE: 99999999, local:deadbeef
```

**关于 PDF 的老实话：** 转文本质量参差。正文一般可用，多栏排版、表格、图注经常乱。
提取不足 500 字符会被判为扫描件并明确报告「需要 OCR」，不会静默入库。
本地条目在送给模型时会标注 `[local full text]`，让它知道这不是经过索引的摘要。

---

## 无人值守运行

文献是持续产出的。靠人记得每周跑一次检索，这件事注定会被忘掉。

```bash
mra watch add "NASH 门脉纤维化 巨噬细胞" --name nash --max 40
mra watch list
mra sync --quiet --max-cost 2.00
```

**检索式只规划一次。** `watch add` 时用模型生成检索式并存下来，`sync` 之后逐字重放。
理由：无人值守时每次重新规划要花钱、结果不确定、而且检索范围会在没人察觉时漂移。
要改检索式就显式改：`mra watch add --name nash --query '<新检索式>' "主题"`。

**两个闸：**
- `--max-cost` 到达上限就干净停下（退出码 2），已抓取的文献保留，下次接着提炼
- 单个 watch 失败不影响其他 watch，错误进简报和 stderr

**简报才是重点。** 知识库自己悄悄变大没有意义。每次 sync 在 `.mra/briefs/YYYY-MM-DD.md`
写一份，按「削弱假说 → 改变问题 → 支持 → 无关」排序——**削弱的排最前面**。
你周一早上要看的就是这个。

### 挂到定时任务

```cron
# 每周一早上 7 点。注意开头的 `. $HOME/.mra-env`——cron 几乎不继承环境变量，
# ANTHROPIC_API_KEY 拿不到是定时任务静默失败的头号原因。
0 7 * * 1 . $HOME/.mra-env && cd $HOME/research && mra sync --quiet --max-cost 2.00 >> $HOME/.mra/sync.log 2>&1
```

`~/.mra-env` 里就一行：`export ANTHROPIC_API_KEY="sk-ant-..."`（记得 `chmod 600`）。

**macOS** 用 launchd 更可靠（cron 在新版 macOS 上需要额外授权）：把同样的命令包成一个
`.sh`，写一份 `~/Library/LaunchAgents/com.mra.sync.plist`，`launchctl load` 加载。

**Windows** 用任务计划程序，操作填 `cmd /c "mra sync --quiet --max-cost 2.00"`，
并在「环境」里确认 `ANTHROPIC_API_KEY` 是**系统级**变量而非仅当前用户会话。

先用 `mra sync --no-digest` 手工跑一次确认检索式对，再挂定时。

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

评分前会先剥掉**不是正文的东西**：`[DATA NEEDED]` / `[CITATION NEEDED]` 标注、
`[PMID:x]` 引用标记、Markdown 的标题与列表符号。这一条不是洁癖——不剥的话，
一份老老实实标出每一个缺失数字的草稿，会因为"6 个句子以 `[data` 开头"被扣分，
而 `polish` 正是由这些扣分项驱动的，等于在劝改写去抹掉那些标记；
可 `writing.py` 又会因为标记被抹掉而告警。工具的两半互相打架。

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

## 花钱这件事

按调用量计费，不是订阅。每条命令跑完都打印这次花了多少、累计多少，`mra usage` 查明细。

`digest` 是最贵的一条——**每篇文献一次调用**。所以它跑之前先按你库里实际存的文本量
估算并打印出来，超过 $1 会先问一句。**没有终端能回答的时候（脚本、定时任务）直接拒绝**，
让你显式加 `--max-cost` 或 `--yes`——静默跑下去正是账单变成意外的方式：

```
Extracting 500 of 500 pending articles (12000k characters).
  Estimated cost: about $43.50

This is estimated at $43.50 and nothing is watching for an answer.
Re-run with --max-cost to set a ceiling, or --yes to accept.
```

`--max-cost` 是硬闸，`digest` 和 `sync` 都有：到上限就干净停下（退出码 2），
已提炼的保留，再跑一次接着做。

---

## 命令速查

| 命令 | 作用 | 需要 API |
|---|---|:---:|
| `mra init` | 建立工作区 | |
| `mra guide` | 中文流程速查 | |
| `mra status` | 当前工作区概况 | |
| `mra search TOPIC` | 规划检索式并抓取 PubMed | ✓ |
| `mra import FILE...` | 导入 PubMed XML / PDF / 纯文本 | XML 不需要 |
| `mra watch add/list/remove` | 保存检索式（供 sync 重放） | add 需要 |
| `mra sync` | 跑完所有 watch，提炼新文献，写简报 | ✓ |
| `mra digest` | 逐篇结构化提炼（先报预估，贵就先问） | ✓ |
| `mra chat [MSG]` | 科学对话（省略 MSG 进入交互） | ✓ |
| `mra hypothesis` | 冻结为带版本的假说 | ✓ |
| `mra hypotheses` / `mra diff A B` | 版本列表 / 逐字段比较 | |
| `mra proposal` | 生成 proposal | ✓ |
| `mra review TOPIC` | 从知识库写综述（先出大纲） | ✓ |
| `mra journal add NAME` | 建立期刊风格档案 | ✓ |
| `mra assess FILE` | 五维打分 + 排序推荐候选期刊 | ✓ |
| `mra assess FILE --journal N` | 对指定期刊的匹配度评估 | ✓ |
| `mra figures FILE` | 规划图表论证（不画图） | ✓ |
| `mra draft SECTION` | 按期刊风格写某一节 | ✓ |
| `mra nativize FILE` | 母语化改写 | ✓ |
| `mra lint FILE` | AI 痕迹静态检查 | |
| `mra polish FILE` | 迭代去 AI 化 | ✓ |
| `mra finalize FILE` | v1 + v2 + 报告 | ✓ |
| `mra refs FILE` | 引用真实性核对 | |
| `mra fingerprint DIR` | 学习你的文风 | ✓ |
| `mra memory --refresh` | 课题方向图谱 | |
| `mra usage` | Token 用量与花费明细 | |
| `mra export` | 全量导出 JSON | |

`import` / `lint` / `refs` / `memory` / `usage` / `status` / `guide` **完全离线**，没有 API key 也能跑。

### 花费可见

每条调用模型的命令结束后会打印本次花费，累计记在 `.mra/usage.json`：

```
usage: 1 call · in 11.4k · cache hit 876, saved ~$0.00 · out 369 · $0.063 · total $0.20
```

`mra usage` 看累计与最近命令明细。价格表是 2026-06 的快照，写死在 `mra/usage.py`；
官方调价或你走机构网关时，在 `mra.config.json` 里覆盖：

```json
{ "prices": { "claude-opus-5": [5.0, 25.0] } }
```

缓存读按输入价的 0.1×、写按 1.25× 计——不区分的话，一次高命中率的运行会被算贵好几倍。

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
  assess.py       数据打分 · 期刊推荐 · 匹配度评估
  writing.py      分节写作、母语化、去 AI 化迭代循环
  deai.py         AI 痕迹静态检测（确定性，离线）
  citations.py    引用真实性核验
  memory.py       方向图谱 + 写作指纹
  ingest.py       PDF/纯文本提取、本地 ID
  brief.py        同步简报
  usage.py        Token 用量与花费
  prompts/*.md    所有提示词，Markdown 明文，可直接改
examples/         示例语料（可直接 mra import）
tests/            217 个测试，全部离线运行
docs/PROPOSAL.md  条款式 + 提纲式方案书
```

**提示词全部是 `mra/prompts/` 下的 Markdown 明文。** 觉得追问太温和、
评估太宽松、去 AI 化改得太多——直接改文件，不用碰 Python。这是有意的设计。

---

## 开发

```bash
pip install -e ".[dev]"
python -m pytest -q          # 217 passed，不需要 API key 和网络
```

测试覆盖：PubMed XML 解析、FTS5 检索与排序、假说版本化、AI 痕迹评分与句子切分、
引用核验、写作保真守卫、方向图谱、以及用 mock transport 校验的 **API 请求线格式**
（模型 id、adaptive thinking、effort、缓存断点、fallback 降级、拒答处理）。
