# 换用别的模型（DeepSeek 等）

默认走 Anthropic。也可以指向任何 **OpenAI 兼容**的端点——DeepSeek 是主要动机，
但同一条路对其他兼容服务一样有效。

```bash
export MRA_PROVIDER=openai
export MRA_BASE_URL=https://api.deepseek.com     # 以对方文档为准
export MRA_API_KEY=sk-你的key
export MRA_MODEL=deepseek-chat

pip install openai        # 只有走这条路才需要
```

**注意 `set` 是 cmd 的语法。** PowerShell 里要写成：

```powershell
$env:MRA_PROVIDER   = "openai"
$env:MRA_BASE_URL   = "https://api.deepseek.com"
$env:MRA_API_KEY    = "sk-你的key"
$env:MRA_MODEL      = "deepseek-chat"
```

两种写法都**只对当前这个窗口有效**，关掉就没了——临时试正好。想长期生效，
cmd 用 `setx`（记得新开窗口），或写进下面的配置文件。

也可以写进工作区的 `mra.config.json`（key 除外，key 只从环境变量读）：

```json
{ "provider": "openai", "base_url": "https://api.deepseek.com", "model": "deepseek-chat" }
```

**接完先自检**，两次极小的调用，几厘钱：

```bash
mra doctor
```

它会告诉你 key 认不认、连不连得通、以及**最关键的一条：这个端点的 tool calling 能不能用**。
不能用的话，下面那张表里"结构化"那一半的命令全都跑不了，它会直接把两份清单列出来。

> **DeepSeek 已实测通过**（`deepseek-chat`，2026-08）：纯文本 1.3s，结构化输出可用，
> 全部命令都能跑。测试环境 Windows + Python 3.14。

**关于 JSON Schema 的一个坑（已处理）：** pydantic 对每个嵌套模型都会生成 `$ref` +
`$defs`，而 DeepSeek 的 tool calling **不解析引用**——给它带 `$defs` 的 schema，
它返回一个空对象 `{}`，不报错。所以 `figures` / `review` / `assess` 这些字段嵌套的命令
会连挂两次，而 `digest`（`LitCard` 是平的）却正常。

现在发送前会把引用**全部内联展开**，代价是请求大一点，好处是任何端点都读得懂，
而且对本来就是平的 schema 是空操作。有测试逐个检查我们发出去的每一个 schema 里
不再残留 `$ref`。

如果你接的是别的兼容端点、又遇到类似的"返回空对象"，先怀疑 schema 那一层。

---

## 能用什么，不能用什么

代码里有两类调用。**纯文本**的到哪都能跑；**结构化输出**的要靠对方支持 tool calling。

| 命令 | 用哪一类 | OpenAI 兼容端点 |
|---|---|---|
| `chat`、`draft`、`nativize`、`polish`、`proposal` | 纯文本 | 直接可用 |
| `digest`、`hypothesis`、`assess`、`review`、`journal add`、`sync` 的简报、`fingerprint`、PDF 元数据、中文检索词 | 结构化 | **要求对方的 tool calling 可用** |

Anthropic 这边，结构化输出是**由 API 约束解码**的——返回的 JSON 一定符合 schema。
OpenAI 兼容这条路没有这个保证，所以做法是：强制一次 tool call → 用 pydantic 校验
→ **不合格就把校验错误原样喂回去重试一次**。近似的错误一般一次就修好；两次还不行就
**报错，而不是返回一个半填的对象**——半填的卡片会静悄悄污染整个知识库。

## 三个会消失的特性

| | Anthropic | OpenAI 兼容 |
|---|---|---|
| **提示词缓存** | 有，能省掉重复前缀的钱 | 没有。`cache_control` 会被剥掉再发（留着轻则被忽略，重则 400）。对方自己缓存的话，我们从 usage 里读回来记账 |
| **effort 控制** | 有 | 没有，参数直接不发 |
| **被拒时的服务端 fallback** | 有 | 没有 |

这三条影响的是**成本和稳健性，不是正确性**。少了它们工具照样跑。

## 花费统计

`usage.py` 的价格表里没有 DeepSeek 的价格——**我没有可靠来源，就不编**。
没有价格时它显示"未知"，不会显示成 0。要看到金额，在 `mra.config.json` 里自己填
（单位：美元 / 百万 token，`[输入, 输出]`）：

```json
{ "prices": { "deepseek-chat": [0.27, 1.10] } }
```

数字以 DeepSeek 官网当前定价为准，填错了统计就是错的。

---

## 一句实话

工具会照样跑，格式一模一样：还是给你一份五维评估、一份稿子、一份综述。
**变的是里面的判断值不值钱，而这件事从输出的样子上看不出来。**

这个工具真正的产出不是格式，是那些判断——比如它在真实数据上指出"加葡萄糖改善了脂质
预测却没改善葡萄糖本身，这是一个无意的特异性阴性对照"，或者"这个 95% CI 的宽度对不上
n=34，汇总单位可能不是样本"。这类东西是模型能力的直接产物。

所以真要换，**别凭感觉**：

```bash
# 同一份数据、同一个库，两边各跑一次
MRA_PROVIDER=anthropic mra assess data.csv -o claude.json
MRA_PROVIDER=openai MRA_MODEL=deepseek-chat mra assess data.csv -o deepseek.json
```

把两份输出并排读。半小时、几毛钱，比任何推演都准。

## 混着用可能更划算

`digest` 是高频、机械、结构化的活——按篇计费，是花钱最多的一步，换便宜模型损失最小。
`chat` / `assess` / `review` 是判断，值得用强的。

现在是按环境变量整体切换，所以做法是分两次跑：

```bash
MRA_PROVIDER=openai MRA_MODEL=deepseek-chat mra digest      # 便宜的做提炼
mra assess data.csv                                          # 默认的做判断
```

（每个命令一个模型的配置还没做。如果这个用法真的常用，值得加一个 `extraction_model`
——`extraction_effort` 已经是分开的，加这个是顺理成章的。）
