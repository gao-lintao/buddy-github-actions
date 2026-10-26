# Buddy 加油站 · GitHub Actions 云端自动签到

把 WorkBuddy「Buddy 加油站」的**每日签到 + 派猫猫旅行**放到 GitHub 免费的 `schedule` 定时任务上，  
用云端 runner 执行，**不需要任何常开设备**。每天北京时间 09:00 自动跑一次，结果推送到微信。

## 设计要点

- **云端无本机登录态**：不读任何本地登录态文件。本机一次性导出**接口令牌(access token，约 28 天有效)**写入仓库 Secret `WORKBUDDY_TOKEN`，  
  Actions 每次运行时直接把它当作 Bearer 令牌使用；同时保留可选的 `WORKBUDDY_REFRESH_TOKEN`（长效令牌）自续期模式。
- **一次运行干两件事**：同一次 job 内先签到、再派猫（先领已到家的旅行积分，再判断今天能否派新趟）。
- **微信分两段**：推送标题区分「签到成功/失败」，正文用 `## 一、签到结论` 与 `## 二、猫猫旅行` 两段分明。
- **猫猫失败不影响签到**：只要签到成功，整体即成功（退出码 0）；猫猫异常只单独记录。
- **令牌安全**：令牌明文绝不打印、绝不进仓库（`.gitignore` 拦截）、绝不进 Actions 日志（输出前递归脱敏）。

> 所有接口名称均对照线上实际请求返回核对，未沿用网传旧接口名。

## 目录结构

```
.github/workflows/daily-points.yml   # 定时任务（UTC 01:00 = 北京 09:00）
scripts/buddy_actions.py             # 云端客户端（纯标准库）
scripts/refresh_secret.sh            # 本机刷新脚本：导出接口令牌 → 写仓库 Secret（有 gh 自动写，无 gh 导出本地文件）
```

## 一、部署步骤

### 1. 推到 GitHub

方式一（推荐，一键）：用内置脚本自动完成 init / add / commit / remote / push。

```bash
REPO=你的用户名/你的仓库 bash scripts/deploy.sh
```

> 首次 `push` 时，GitHub 已不支持账户密码，请用**个人访问令牌(PAT)**作为密码粘贴（勾选 `repo` 权限）。  
> macOS 会把它存入钥匙串，下次免输。想用 SSH 的话，自行把 remote 改成 `git@github.com:用户名/仓库.git`。

方式二（手动）：

```bash
git init
git add -A
git commit -m 'add buddy daily points actions'
git remote add origin https://github.com/你的用户名/你的仓库.git
git push -u origin HEAD
```

> 注意：先到 GitHub 网页**新建一个空仓库**（不要勾选 README / .gitignore / license，保持为空），拿到仓库地址再 push；分支名不一定是 `main`，用 `HEAD` 自动适配。

### 2. 本机写入接口令牌 Secret（每 3~4 周重跑一次）

```bash
REPO=你的用户名/你的仓库 bash scripts/refresh_secret.sh
```

> 该脚本读取本机 WorkBuddy 登录态、解密导出**接口令牌(access token)**并写入 `WORKBUDDY_TOKEN` Secret。  
> 接口令牌约 28 天有效，到期或运行报「令牌失效」时重跑本脚本覆盖即可。  
> 前置：本机已登录 WorkBuddy 即可；`gh` CLI **可选**——
> - 已装 `gh` 且 `gh auth login` 过：脚本自动 `gh secret set` 写入；
> - 未装 `gh`（本机当前即如此）：脚本把令牌导出到本地 `.env.local`（已被 `.gitignore` 忽略，不入库、不打印明文），并给出到 GitHub 网页手动添加 Secret 的步骤，添加后建议 `rm .env.local` 清除本地明文。
>
> 进阶（set-and-forget）：若你能导出长效令牌，也可写入 `WORKBUDDY_REFRESH_TOKEN`，  
> 云端会实时刷新接口令牌；详见技能通道 C 说明。默认主用 `WORKBUDDY_TOKEN`。

### 3. 配置微信推送 Secret（任选其一）

- **ServerChan（推荐，免费推到微信服务通知）**：在 https://sct.ftqq.com 绑定微信，  
  把 `SID` 或 Turbo 的 `SCTxxxx` 填入仓库 Secret **`SERVERCHAN_SCKEY`**。
- **PushPlus**：在 <https://www.pushplus.plus> 获取 token，填入 Secret **`PUSHPLUS_TOKEN`**。

### 4. 开启定时任务

在仓库 **Settings → Actions → General** 确认 `Workflow permissions` 允许运行；  
`schedule` 默认开启，无需额外操作。也可在 Actions 页面点 **Run workflow** 手动触发验证。

## 二、验证一次成功运行（本地）

本地可用与云端完全相同的代码路径跑一次（用本机导出的接口令牌，不依赖云端）：

```bash
TOKEN=$(python3 - ~/.workbuddy/skills/totorosir-workbuddy-score/scripts <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import buddy_station as bs
print(bs._resolve_auth("auto")[0])
PY
)
WORKBUDDY_TOKEN="$TOKEN" SERVERCHAN_SCKEY=xxxx \
  python3 scripts/buddy_actions.py
```

## 三、常见问题

- **令牌失效**：重新运行 `refresh_secret.sh` 覆盖 Secret 即可（长效令牌对应账号钥匙，请注意保管）。
- **时区**：GitHub cron 为 UTC，`0 1 * * *` 即北京 09:00；如需改时间改这一行即可。
- **猫猫当天已派**：脚本检测到 `daily_limit_reached` 或 `traveling` 会自动跳过新趟，不影响签到。
- **微信没收到**：检查对应 Secret 是否填对；客户端日志会打印推送结果。
