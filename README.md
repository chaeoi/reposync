# RepoSync

RepoSync 是一个面向容器运行的 Git 强制单向镜像服务。一个 YAML 可以配置多个 `source`（A）到 `target`（B）的任务，每个端点可以使用不同平台、账号和密码。A 是唯一权威；每次成功同步后，B 的所有分支和标签都与本次确认的 A 快照完全一致。

代码分层和同步周期见 [`docs/architecture.md`](docs/architecture.md)。

它只同步 Git 分支和标签，不复制 issue、pull request、release、默认分支设置、保护规则等平台专属数据。

## 强制镜像规则

每个任务会比较 A/B 的所有分支和标签，然后通过一次原子 push 更新 B：

- A 有、B 没有：在 B 创建。
- A/B 指向不同对象：用 A 强制覆盖 B，包括 B 超前和历史分叉。
- B 有、A 没有：从 B 删除。
- A/B 相同：不操作。

所有创建、覆盖和删除都放在一个 `git push --atomic` 中。同一仓库的任一 ref 更新失败，整批操作失败，不留下部分更新。`--force-with-lease` 会阻止检查后发生的 B 端并发写入；push 后程序会重新抓取 A/B 校验，期间若又发生变更最多重新镜像三次，只有 refs 完全相同才报告成功。

因此不再支持 `branches`、`tags`、`conflict` 或 `deletion_policy` 配置。部分过滤无法保证 B 完全等于 A。

注意：目标平台的受保护分支、禁止删除默认分支等服务端策略仍可能拒绝强推。此时原子 push 会整体失败，需要先调整 B 的平台权限或保护规则。

## 配置

复制 [`config.example.yml`](config.example.yml) 为 `config.yml`，直接在 YAML 中填写账号、密码或访问密钥。建议执行 `chmod 600 config.yml`，并不要把实际配置提交到 Git。GitHub、GitLab、Gitee、Bitbucket 和 Codeberg 可通过 `platform + repository` 生成 URL。Gitea 使用 `base_url + repository`：

```yaml
version: 1
interval: 5m
workdir: /var/lib/reposync

repositories:
  - name: github-to-gitea
    source:
      platform: github
      repository: org/project
      username: github-user
      password: replace-with-github-token
    target:
      platform: gitea
      base_url: https://gitea.example.com
      repository: backup/project
      username: gitea-user
      password: replace-with-gitea-password-or-token
```

自建服务或特殊仓库路径可以直接指定 URL：

```yaml
target:
  platform: custom
  url: https://git.example.com/team/project.git
  username: sync-user
  password: replace-with-target-password-or-token
```

## 账号和密码

端点直接设置 `username`/`password`，也可以在顶层定义后通过 `credential` 复用。两种方式最终都会从 YAML 读取凭据：

```yaml
credentials:
  backup-account:
    username: backup-user
    password: replace-with-password-or-token

repositories:
  - name: project
    source:
      platform: github
      repository: org/project
      credential: backup-account
    target:
      platform: gitea
      base_url: https://gitea.example.com
      repository: mirror/project
      credential: backup-account
```

支持 `password: plain-password` 明文密码，`password` 字段也用于 PAT/access token。请只在受保护的 `config.yml` 中填写真实值，不要提交到 Git：

- Gitea 未启用 MFA 且服务端允许 HTTP 密码认证时，可以使用账户密码；启用 MFA 后应使用 access token。
- GitHub 不接受账户密码执行 HTTPS Git 操作，需要填写 Personal Access Token。
- GitLab 开启 2FA/SAML 时必须使用 token；其他情况取决于实例配置。

URL 中禁止嵌入密码。凭据通过 Git askpass 传递，日志不会打印密码。

## 命令

```bash
python -m pip install .
reposync validate --config ./config.yml
reposync sync --config ./config.yml --dry-run
reposync sync --config ./config.yml
reposync run --config ./config.yml
```

`sync` 执行一轮，`run` 按 `interval` 持续镜像。`--dry-run` 只输出将创建、覆盖和删除的 refs。

## Docker

镜像内置 Git 和 RepoSync，默认以非 root 用户运行。缓存位于 `/var/lib/reposync`，建议使用命名卷持久化。配置文件中已包含账号、密码/token，直接挂载该文件运行：

```bash
cp config.example.yml config.yml
# 编辑 config.yml，填写真实账号和密码/token
chmod 600 config.yml
docker volume create reposync-data
docker run -d \
  --name reposync \
  --restart unless-stopped \
  -v "$PWD/config.yml:/etc/reposync/config.yml:ro" \
  -v reposync-data:/var/lib/reposync \
  ghcr.io/your-org/reposync:latest
```

本地构建镜像时，将最后一行替换为 `reposync:local`。查看日志使用 `docker logs -f reposync`，停止并删除容器使用 `docker rm -f reposync`；命名卷不会随容器删除。

## GitHub Actions

`.github/workflows/docker.yml` 会先运行真实本地 Git 仓库集成测试，再为 amd64 和 arm64 构建镜像。`main` 和版本标签会推送到 `ghcr.io/<owner>/<repo>`，pull request 只构建、不推送。

## 开发与测试

```bash
PYTHONPATH=src python -m unittest discover -v
PYTHONPATH=src python -m compileall -q src
docker build -t reposync:local .
```

项目采用 MIT 许可证。
