# 使用基础镜像
FROM langgraph_agent_env

# 设置 LANG 环境变量为 zh_CN.UTF-8
ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8

# 设置工作目录
WORKDIR /app

# 只复制源码，不安装依赖
# 这样当代码变化时不会触发依赖重新安装
COPY . .

EXPOSE 18887

# 运行python
ENTRYPOINT ["poetry", "run", "python", "main.py"]