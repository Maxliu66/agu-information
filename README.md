# 爱股票要闻 -> 飞书推送

自动抓取爱股票(aigupiao.com)快讯/要闻，推送到飞书群机器人。通过 GitHub Actions 定时运行，**电脑关机也能自动推送**。

## 功能

- 每5分钟自动轮询爱股票快讯API
- 按模式过滤要闻（all/important/hot/app_push/broad）
- 增量去重，已推送的不再重复
- 推送飞书群机器人webhook
- 状态持久化（通过git commit）
- 凌晨0-6点静默，减少噪音

## 部署步骤

### 1. Fork 或创建此仓库

### 2. 配置 GitHub Secrets

在仓库 Settings -> Secrets and variables -> Actions 中添加：

| Secret 名称 | 值 | 说明 |
|---|---|---|
| `FEISHU_WEBHOOK_URL` | `https://open.feishu.cn/open-apis/bot/v2/hook/xxx` | 飞书机器人Webhook地址 |

### 3. 手动触发一次验证

进入 Actions -> Feishu News Push -> Run workflow，确认推送正常。

### 4. 自动运行

GitHub Actions 会每5分钟自动运行（cron: `*/5 * * * *`）。

> **注意**：GitHub对新建仓库的scheduled workflow可能有延迟，首次需要手动触发一次激活。

## 过滤模式

通过环境变量 `FILTER_MODE` 控制：

| 模式 | 条数/天 | 说明 |
|---|---|---|
| `app_push` | 3-5 | 仅APP推送标记的要闻 |
| `important` | 5-10 | 重要+推送+重要DB |
| `hot` | 10-20 | 24h热门+重要+推送 |
| `broad` | 30-50 | hot+重要DB+阅读量>5000 |
| `all` | 100+ | 全部快讯 |

## 本地运行

```bash
# 设置Webhook
export FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# 单次运行
python aigupiao_feishu_push.py --once

# 持续运行（每5分钟）
python aigupiao_feishu_push.py --continuous 300

# 测试（不推送，只打印）
python aigupiao_feishu_push.py --test
```
