# RepoSync

RepoSync 用 A（`source`）强制镜像到 B（`target`），一个 YAML 可以配置多个仓库任务，支持 GitHub、GitLab、Gitee、Gitea、Bitbucket、Codeberg 和自定义 Git URL。

同步范围是所有分支和标签：B 缺少的会创建，不同的会被 A 覆盖，B 独有的会删除。

## 1. 配置

```bash
cp config.example.yml config.yml
chmod 640 config.yml
```

在 `config.yml` 中直接填写账号、密码或 token：

```yaml
repositories:
  - name: github-to-gitea
    source:
      platform: github
      repository: org/project
      username: github-user
      password: github-token
    target:
      platform: gitea
      base_url: https://gitea.example.com
      repository: backup/project
      username: gitea-user
      password: gitea-password-or-token
```

也可以在顶层定义 `credentials`，然后在端点使用 `credential` 复用账号。真实密码不要提交到 Git。

## 2. Docker 运行

```bash
docker volume create reposync-data
docker run -d \
  --name reposync \
  --restart unless-stopped \
  --group-add "$(stat -c '%g' config.yml)" \
  -v "$PWD/config.yml:/etc/reposync/config.yml:ro" \
  -v reposync-data:/var/lib/reposync \
  your-dockerhub-username/reposync:latest
```

`config.yml` 不参与镜像构建，只在容器启动时挂载。`--group-add` 让容器内的非 root 用户能够读取权限为 `0640` 的配置文件。

```bash
docker logs -f reposync
docker rm -f reposync
```

本地构建和校验配置：

```bash
docker build -t reposync:local .
docker run --rm \
  --group-add "$(stat -c '%g' config.yml)" \
  -v "$PWD/config.yml:/etc/reposync/config.yml:ro" \
  reposync:local validate
```

## 3. 镜像发布

GitHub Actions 仅用于发布 Docker 镜像。先在仓库的 `Settings -> Secrets and variables -> Actions` 中添加：

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

推送到 `main` 或手动触发 workflow 后，会发布：

```text
DOCKERHUB_USERNAME/reposync:latest
DOCKERHUB_USERNAME/reposync:YYYYMMDD
```

日期标签使用 commit 的 UTC 提交日期。

GitHub 不支持账户密码进行 HTTPS Git 操作，应使用 Personal Access Token；Gitea/GitLab 开启 MFA/2FA 时同样使用 token。

## 4. 架构

### 目录职责

```text
core/
├── __main__.py              python -m core 入口
├── cli.py                   参数解析、日志、run 生命周期
├── config.py                YAML 解析、校验、平台 URL 和凭据模型
├── git.py                   Git 子进程、askpass 和超时处理
└── mirror.py                A -> B 强制镜像、原子 push、回读校验

config.example.yml           多任务配置示例
Dockerfile                   镜像构建和运行入口
.github/workflows/docker.yml 构建、发布镜像
```

依赖方向保持单向：

```text
cli -> mirror -> git
       └-------> config
```

`cli.py` 只负责命令和运行周期，`mirror.py` 负责同步规则，`git.py` 只执行 Git 命令，YAML 解析和数据模型集中在 `config.py`。

### 同步周期

每个任务使用独立的 bare cache 和文件锁：

1. 抓取 source 和 target 的全部 `refs/heads/*`、`refs/tags/*`。
2. 计算差异：创建 source 新 ref、覆盖不同对象、删除 target 独有 ref。
3. 使用一次 `git push --atomic` 和每个 ref 的 `--force-with-lease` 提交全部变更。
4. 再次抓取两端，只有 refs 完全相同才返回成功；两端在周期中变化时最多重新稳定三次。

`--atomic` 保证单个仓库任务不会出现部分更新。平台的分支保护或禁止删除策略可能拒绝该操作，此时整个 push 失败，source 不会被反向修改。

### 配置边界

每个 `repositories` 项只有一个方向：`source` 是权威端，`target` 是镜像端。当前同步范围固定为所有分支和标签，不能用过滤字段制造“部分镜像”。账号密码可以直接写在端点，或通过同一个 YAML 中的 `credentials` 区块复用。
