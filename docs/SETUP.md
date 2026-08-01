# 安装与上手（给第一次装的人）

这份文档假设你**没有**用过命令行工具，从零开始。装完大约 10 分钟，其中大半是等下载。

主 README 讲的是这个工具能干什么、为什么这么设计；这份只讲怎么在你自己的电脑上跑起来。

---

## 你需要准备的两样东西

| 东西 | 在哪拿 | 花钱吗 |
|---|---|---|
| **Python ≥ 3.10** | https://www.python.org/downloads/ | 免费 |
| **Anthropic API key** | https://platform.claude.com → Settings → API Keys | 按用量计费 |

关于费用：**这不是订阅制**，是按调用量算的。以下是实测值（Opus 5，effort=high）：

| 命令 | 一次花多少 |
|---|---|
| `mra assess`（评估 + 推荐期刊） | $0.27 – $0.32 |
| `mra journal add`（期刊建档） | $0.17 |
| `mra chat`（一轮对话） | $0.06 – $0.07 |
| `mra digest` | **每篇文献一次调用** —— 总价取决于你库里有多少篇 |

工具每条命令跑完都会打印这次花了多少、累计多少，随时 `mra usage` 查明细。
刚开始建议在 platform 上给 key 设一个月度上限。

`lint`、`refs`、`memory`、`usage`、`status`、`guide` 这几条命令**不花钱**，
`import` 处理 `.xml` 时也不花钱 —— 可以先用它们熟悉手感。

---

## Windows

### 1. 装 Python

去 https://www.python.org/downloads/ 下载安装包。

> **安装第一屏必须勾上 `Add python.exe to PATH`**（在窗口最下面）。
> 忘了勾的话，后面每条命令都会报「不是内部或外部命令」。勾漏了就重新运行安装包，
> 选 Modify 补上。

装完打开 **PowerShell**（开始菜单搜 powershell），输入：

```powershell
python --version
```

看到 `Python 3.10` 或更高就对了。

### 2. 装工具

```powershell
cd $HOME\Documents
git clone https://github.com/Alexsheng26/Medical-agent.git
cd Medical-agent
pip install -e .
```

没装 git 的话，去仓库页面点 **Code → Download ZIP**，解压后 `cd` 进那个文件夹，
直接跑 `pip install -e .` 也一样。

### 3. 设 API key

```powershell
setx ANTHROPIC_API_KEY "sk-ant-你的key"
```

`setx` 是**永久**写入，只需要做一次。但它对**当前这个窗口不生效** —— 关掉 PowerShell
重新开一个，然后验证：

```powershell
echo $env:ANTHROPIC_API_KEY
```

### 4. 初始化

```powershell
mra init --email 你的邮箱@学校.edu.cn
mra guide
```

邮箱是 NCBI 的要求（他们用它联系滥用检索的人），不会发给别人。

**如果提示 `mra` 不是命令**：pip 装的可执行文件不在 PATH 上。用这个等价写法，永远有效：

```powershell
python -m mra guide
```

下面所有 `mra xxx` 都可以换成 `python -m mra xxx`。

---

## macOS

```bash
# 系统自带的 Python 版本通常太老，用 Homebrew 装一个
brew install python@3.12

cd ~/Documents
git clone https://github.com/Alexsheng26/Medical-agent.git
cd Medical-agent
pip3 install -e .

# 写进 shell 配置，以后每个终端都有
echo 'export ANTHROPIC_API_KEY="sk-ant-你的key"' >> ~/.zshrc
source ~/.zshrc

mra init --email 你的邮箱@学校.edu.cn
```

没有 Homebrew 就先装它：https://brew.sh

---

## Linux

```bash
git clone https://github.com/Alexsheng26/Medical-agent.git
cd Medical-agent
pip install -e .

echo 'export ANTHROPIC_API_KEY="sk-ant-你的key"' >> ~/.bashrc
source ~/.bashrc

mra init --email 你的邮箱@学校.edu.cn
```

---

## 第一次跑：用仓库自带的示例语料

仓库里有 8 篇示例文献（关于 NASH 肝纤维化，其中**故意放了互相矛盾的证据**），
不联网不花钱就能试：

```bash
mra import examples/demo_corpus.xml
mra status
```

看到 `Articles      8` 就成功了。接着可以：

```bash
mra digest          # 逐篇结构化提炼（8 篇 = 8 次调用）
mra chat            # 进入对话，试着说「这批文献里最大的矛盾是什么」
```

想清空重来：直接删掉工作目录里的 `.mra` 文件夹即可，不会影响别的东西。

---

## 工作目录的概念

`mra` 的所有数据都存在**你运行命令时所在目录**下的 `.mra/` 文件夹里：

```
你的课题文件夹/
├── .mra/
│   ├── knowledge.db      ← 文献库、假说、期刊档案，全在这一个 SQLite 文件里
│   ├── mra.config.json   ← 配置（不含 key，key 只从环境变量读）
│   ├── drafts/           ← 生成的稿件
│   ├── briefs/           ← 无人值守跑批写的简报
│   └── usage.json        ← 花费记录（第一次调用模型后才出现）
├── data.csv
└── my_papers/
```

这意味着：

- **一个课题一个文件夹。** 在 `~/研究/肝纤维化/` 下跑，和在 `~/研究/胰腺癌/` 下跑，
  是两个完全独立的知识库，互不干扰。
- **备份就是拷贝这个文件夹。** 换电脑把整个文件夹拷过去，接着用。
- **数据不出本地。** 只有你主动敲的那条命令会把内容发给模型，其余全在硬盘上。

想指定别的位置：加 `--workspace`，注意它要放在**子命令前面**：

```bash
mra --workspace ~/研究/肝纤维化/.mra  status     # ✓
mra status --workspace ~/研究/肝纤维化/.mra      # ✗ 会报 unrecognized arguments
```

---

## 常见问题

**`mra` 不是内部或外部命令 / command not found**
用 `python -m mra` 代替 `mra`，功能完全一样。根因是 pip 的脚本目录不在 PATH 上。

**`No API credentials found`**
key 没设成功，或者设完没重开终端。Windows 上 `setx` 对当前窗口不生效，必须新开一个。

**PubMed 连不上 / `mra search` 超时**
学校网络或防火墙挡了 NCBI。绕过办法：在浏览器里正常搜 PubMed，
**Send to → File → Format: XML → Create File**，然后

```bash
mra import ~/Downloads/pubmed_result.xml --topic "你的主题"
```

效果完全一样，而且这条路不花钱。

**PDF 导入后内容是乱的**
PDF 转文本本来就参差：正文一般可用，**多栏排版、表格、图注经常乱**。
提取不足 500 字符会被判定为扫描件并明确提示「需要 OCR」，不会静默入库。
本地文献送给模型时会带 `[local full text]` 标记，让它知道这不是经过索引的摘要。

**PowerShell 里 `mra import ~/papers/*.pdf` 只导入了一个文件 / 报找不到文件**
PowerShell 不像 bash 那样自动展开通配符。改成：

```powershell
mra import (Get-ChildItem ~\papers\*.pdf).FullName --topic "你的主题"
```

**中文显示成方块**
换一个带中文字形的等宽字体（Windows Terminal：设置 → 外观 → 字体）。
这只影响显示，不影响存进 `.mra/` 里的数据 —— 文件本身一直是 UTF-8。
输出重定向到文件（`mra refs draft.md > out.txt`、`mra sync >> sync.log`）
已经强制走 UTF-8，不会再报编码错。

**`is not UTF-8` 报错**
你的稿件是 GBK 存的。用记事本打开 → 另存为 → 编码选 UTF-8；
或 VS Code 里点右下角编码 → Save with Encoding → UTF-8。
工具**故意不做静默转码** —— 悄悄替换掉几个字符，等于把你要投出去的稿子改坏了。

**花超了怎么办**
`mra usage` 看明细。无人值守跑批一定要加 `--max-cost`：
`mra sync --max-cost 2.00`，到上限就干净停下，已抓的文献保留，下次接着提炼。

---

## 下一步

跑通之后回主 [README](../README.md) 看完整工作流，或者直接：

```bash
mra guide
```

真正的用法是这条链路：**建库 → 磨假说 → 评估数据并选刊 → 学期刊风格 → 分节写作 →
母语化 → 去 AI 化**。第一次建议按 `mra guide` 的顺序走一遍，每一步都看看输出，
不要跳步 —— 后面每一步的质量都取决于前面喂进去了什么。
