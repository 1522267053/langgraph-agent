"""全局时间常量"""

# 等待用户响应的超时秒数（工具确认 / 问题反问共用），超时后自动取消或标记过期
USER_RESPONSE_TIMEOUT_SECONDS = 3600

# 登录失败锁定时长（IP 维度，锁定期间拒绝登录尝试）
LOGIN_LOCK_SECONDS = 300

# 404 速率限制封禁时长（IP 维度，触发阈值后封禁，超时自动解封）
RATE_LIMIT_BLOCK_SECONDS = 300

# 全局配置（AI / 市场配置）内存缓存 TTL，过期后下次读取时刷新
GLOBAL_CONFIG_CACHE_TTL_SECONDS = 300

# 已完成 Agent run 在内存中的保留时长（供断线重连订阅回放，超时清理）
COMPLETED_RUN_RETENTION_SECONDS = 60

# Shell 后台任务完成后在内存中的保留时长（超时清理，结果不再可查）
SHELL_TASK_EXPIRE_SECONDS = 300

# 记忆整理（hot 超限触发 AI 总结）的冷却时长，防止频繁整理
MEMORY_CONSOLIDATION_COOLDOWN_SECONDS = 300.0
