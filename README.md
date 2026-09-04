# arXiv Robotics Daily

每天自动从 arXiv 抓取最新论文，按个人机器人研究兴趣进行关键词评分筛选，并通过邮件发送精简列表：**标题、作者、arXiv 链接**。

## 关注方向

当前规则覆盖：

- UAV / Drone / Quadrotor / Aerial Robotics
- Quadruped / Legged Robotics
- Humanoid Robotics
- Robot Manipulation / Robotic Arm
- Dexterous Hand / Dexterous Manipulation
- Mobile Manipulation / Loco-Manipulation
- VLA / Robot Foundation Models
- Embodied AI / Robot Reasoning
- Robot World Models
- Navigation / Path Planning / Trajectory Optimization
- Active Perception / Exploration / FOV-aware Planning
- SLAM / Mapping / 3D Scene Representation
- Traversability / Terrain Perception / Stairs / Negative Obstacles
- Tactile / Affordance
- Imitation Learning / RL / Diffusion Policy / Flow Policy / Sim-to-Real
- Whole-body Control / MPC / Locomotion Control

低优先级方向（医疗机器人、康复机器人、HRI、群体机器人、软体机器人、纯自动驾驶等）会被降权，但不会绝对屏蔽，因此方法特别相关的论文仍有机会保留。

## 数据来源

GitHub Action 会读取以下 arXiv RSS 分类并去重：

- `cs.RO` — Robotics
- `cs.AI` — Artificial Intelligence
- `cs.LG` — Machine Learning
- `cs.CV` — Computer Vision
- `eess.SY` — Systems and Control

筛选时使用 **Title + Abstract**，邮件中只显示 Title / Authors / Link。

## 自动运行时间

`.github/workflows/daily.yml` 当前设置为：

- 周一至周五
- 北京时间 **10:15**
- 同时支持 GitHub Actions 页面手动运行

手动运行时：

- `send_email = false`：只在 Actions 日志预览，不发邮件
- `send_email = true`：实际发送邮件

## 第一次使用：配置 GitHub Secrets

打开仓库：

`Settings -> Secrets and variables -> Actions -> New repository secret`

至少添加下面 3 个 Secret：

| Secret | 内容 |
|---|---|
| `MAIL_USER` | 发件邮箱，例如 `xxx@qq.com` |
| `MAIL_PASSWORD` | 邮箱 SMTP 授权码 / App Password，不是普通登录密码 |
| `MAIL_TO` | 收件邮箱，可以与发件邮箱相同 |

### QQ 邮箱

QQ 邮箱会自动使用：

- SMTP host: `smtp.qq.com`
- port: `465`
- SSL

需要先在 QQ 邮箱设置中开启 SMTP 服务并生成**授权码**，把授权码填入 `MAIL_PASSWORD`。

### Gmail

Gmail 会自动使用：

- SMTP host: `smtp.gmail.com`
- port: `465`
- SSL

`MAIL_PASSWORD` 应使用 Google App Password。

### Outlook / Hotmail

会自动使用 `smtp-mail.outlook.com:587` + STARTTLS。

### 其他邮箱

可额外增加：

| Secret | 示例 |
|---|---|
| `SMTP_HOST` | `smtp.example.com` |
| `SMTP_PORT` | `465` 或 `587` |
| `SMTP_MODE` | SSL 留空；STARTTLS 填 `starttls` |

## 测试

1. 配好 Secrets。
2. 打开仓库的 **Actions**。
3. 选择 **Daily arXiv Robotics Digest**。
4. 点击 **Run workflow**。
5. 第一次建议 `send_email = false`，检查日志中的筛选结果。
6. 再运行一次并设置 `send_email = true`，确认收到邮件。

## 修改筛选兴趣

只需要编辑 [`keywords.yaml`](./keywords.yaml)。

主要参数：

```yaml
threshold: 5
title_bonus: 2
```

- `threshold` 越高：论文越少、越精准。
- `threshold` 越低：论文越多、召回率越高。
- `title_bonus`：关键词直接出现在标题时额外加分。

每个兴趣组都有独立权重。例如：

```yaml
vla_foundation_models:
  weight: 9
  terms:
    - vision-language-action
    - robot foundation model
```

如果以后对新的方向感兴趣，直接把关键词添加到对应组即可。

## 本地运行

```bash
pip install -r requirements.txt
python fetch_and_mail.py --dry-run
```

`--dry-run` 不需要邮箱 Secrets，只会在终端输出当天筛选结果和邮件预览。

## 文件结构

```text
.
├── .github/workflows/daily.yml
├── fetch_and_mail.py
├── keywords.yaml
├── requirements.txt
└── README.md
```
