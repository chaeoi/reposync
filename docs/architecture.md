# RepoSync 架构

## 目录职责

```text
src/reposync/
├── __main__.py              python -m reposync 入口
├── cli.py                   参数解析、日志、run 生命周期
├── config.py                YAML 解析、校验、平台 URL 和凭据模型
├── adapters/
│   └── git.py               Git 子进程、askpass 和超时处理
└── services/
    └── mirror.py            A -> B 强制镜像、原子 push、回读校验

tests/
├── unit/test_config.py      配置和认证模型测试
└── integration/test_mirror.py
                              本地 bare 仓库端到端测试

docs/architecture.md         本文档
config.example.yml           多任务配置示例
Dockerfile                   镜像构建和运行入口
.github/workflows/docker.yml 测试、构建、发布
```

依赖方向保持单向：

```text
cli -> services.mirror -> adapters.git
  └-> config
```

业务层不直接解析 YAML，Git 适配层不理解 source/target 业务规则。以后增加 SSH、代理或其他 Git 执行方式时，只需要扩展 `adapters`；增加配置字段时集中修改 `config.py`。

## 同步周期

每个任务使用独立的 bare cache 和文件锁：

1. 抓取 source 和 target 的全部 `refs/heads/*`、`refs/tags/*`。
2. 计算差异：创建 source 新 ref、覆盖不同对象、删除 target 独有 ref。
3. 使用一次 `git push --atomic` 和每个 ref 的 `--force-with-lease` 提交全部变更。
4. 再次抓取两端，只有 refs 完全相同才返回成功；两端在周期中变化时最多重新稳定三次。

`--atomic` 保证单个仓库任务不会出现部分更新。平台的分支保护或禁止删除策略可能拒绝该操作，此时整个 push 失败，source 不会被反向修改。

## 配置边界

每个 `repositories` 项只有一个方向：`source` 是权威端，`target` 是镜像端。当前同步范围固定为所有分支和标签，不能用过滤字段制造“部分镜像”。账号密码可以直接写在端点，或通过同一个 YAML 中的 `credentials` 区块复用。
