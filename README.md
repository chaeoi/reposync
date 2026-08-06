# RepoSync

RepoSync 用 A（`source`）强制镜像到 B（`target`），一个 YAML 可以配置多个仓库任务，支持 GitHub、GitLab、Gitee、Gitea、Bitbucket、Codeberg 和自定义 Git URL。

同步范围是所有分支和标签：B 缺少的会创建，不同的会被 A 覆盖，B 独有的会删除。配置格式和内部结构说明见 [`docs/architecture.md`](docs/architecture.md)。

## 1. 配置

```bash
cp config.example.yml config.yml
chmod 600 config.yml
```

在 `config.yml` 中直接填写账号、密码或 token：

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
      password: github-token
    target:
      platform: gitea
      base_url: https://gitea.example.com
      repository: backup/project
      username: gitea-user
      password: gitea-password-or-token
```

也可以在顶层定义 `credentials`，然后在端点使用 `credential` 复用账号。真实密码不要提交到 Git。

## 2. 本地运行

```bash
python -m pip install .
reposync validate --config ./config.yml
reposync sync --config ./config.yml --dry-run
reposync sync --config ./config.yml
reposync run --config ./config.yml
```

`sync` 执行一轮，`run` 按 `interval` 持续运行。

## 3. Docker 运行

```bash
docker volume create reposync-data
docker run -d --name reposync --restart unless-stopped -v "$PWD/config.yml:/etc/reposync/config.yml:ro" -v reposync-data:/var/lib/reposync your-dockerhub-username/reposync:latest
```

```bash
docker logs -f reposync
docker rm -f reposync
```

本地构建镜像：

```bash
docker build -t reposync:local .
```

## 4. GitHub Actions

在仓库的 `Settings -> Secrets and variables -> Actions` 中添加：

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

推送到 `main` 或手动触发 workflow 后，会发布：

```text
DOCKERHUB_USERNAME/reposync:latest
DOCKERHUB_USERNAME/reposync:YYYYMMDD
```

日期标签使用 commit 的 UTC 提交日期。

GitHub 不支持账户密码进行 HTTPS Git 操作，应使用 Personal Access Token；Gitea/GitLab 开启 MFA/2FA 时同样使用 token。

