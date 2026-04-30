你是一个资深 Python 架构师，请帮我在 **nanobot 技能体系基础上** 实现一个 Agent Orchestrator。

⚠️ 重要前提：

* 已存在 nanobot skills 系统（skills/ 目录）
* 已支持技能自动热加载
* 已支持通过 exec 调用脚本
* ❌ 不允许重新实现 skill loader / skill runtime
* ❌ 不允许创建新的 skill 系统替代 nanobot

你的任务是：

👉 **只实现调度层（Orchestrator），而不是重写技能系统**

---

【目标】

构建一个：

* Workflow 固定
* Skill 动态选择（基于关键词）
* 支持多技能冲突处理
* 支持复杂任务（SubAgent）

---

【你需要做的】

只实现以下模块：

---

1）Orchestrator（核心）

文件：orchestrator/orchestrator.py

负责：

* 接收用户输入
* 调用 skill_selector
* 调用已有 nanobot skill（通过 exec 或已有接口）

---

2）Skill Selector（重点）

文件：orchestrator/skill_selector.py

⚠️ 注意：

* 不要自己加载 skills
* 不要扫描目录

👉 假设系统已有：

```python
available_skills = [
    {
        "name": "arima-forecast",
        "desc": "...",
        "keywords": ["预测"],
    },
    {
        "name": "bollinger",
        "keywords": ["布林"]
    }
]
```

你只需要：

* 做 keyword 匹配
* 做冲突处理
* 决定调用哪个 skill

---

3）SubAgent（受控）

文件：orchestrator/subagent.py

用于复杂任务：

* 限制 max_steps = 3
* 工具来源 = available_skills
* 输出 JSON

---

4）Skill 调用方式

统一用：

```python
run_skill(skill_name, text)
```

（假设这是 nanobot 提供的能力）

❌ 不要直接写 python xxx.py
❌ 不要实现 exec 逻辑

---

5）复杂任务判断

实现函数：

```python
def is_complex(text: str) -> bool
```

规则：

* 包含“找出 / 筛选 / 排名”
* 多动作组合（分析 + 预测）

---

6）LLM 辅助函数（mock即可）

```python
def llm_select_skill(candidates, text): ...
```

---

---

【禁止事项】

❌ 不要实现 skill loader
❌ 不要扫描 skills 目录
❌ 不要重新定义 skill 结构
❌ 不要替代 nanobot

---

【输出要求】

按文件输出完整代码：

=== file: xxx.py ===

必须可运行，不要伪代码
