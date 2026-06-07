# 使用基础镜像
FROM library/python:3.12.10-slim-bullseye

# 设置 LANG 环境变量为 C.UTF-8
ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8
# （可选）设置时区，避免安装过程中交互式提示
ENV TZ=Asia/Shanghai

# 安装 Poetry
RUN pip install poetry -i https://mirrors.cloud.tencent.com/pypi/simple/

# 设置工作目录
WORKDIR /app

# 复制项目配置文件（这些文件不经常变化）
COPY pyproject.toml poetry.toml README.md ./

# 安装依赖到独立层，只有当配置文件变化时才重新安装
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-root

# 设置 Playwright 使用国内镜像源并安装浏览器（无头版本）及其系统依赖
# 配置 Debian 使用国内镜像源加速系统依赖下载
# RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list \
#     && sed -i 's/security.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list \
#     && apt-get update

# 👇 关键：安装 Playwright 运行 Chromium 所需的系统依赖（官方推荐列表）
# RUN apt-get install -y --no-install-recommends \
#         libglib2.0-0 \
#         libnss3 \
#         libatk1.0-0 \
#         libatk-bridge2.0-0 \
#         libcups2 \
#         libdrm2 \
#         libxkbcommon0 \
#         libxcomposite1 \
#         libxdamage1 \
#         libxfixes3 \
#         libxrandr2 \
#         libgbm1 \
#         libasound2 \
#         libpango-1.0-0 \
#         libcairo2 \
#         ca-certificates \
#         fonts-liberation \
#         libappindicator3-1 \
#         libnspr4 \
#         xdg-utils \
#         libexpat1 \
#         xvfb \
#         libxt6 \
#         libxss1 \
#         libx11-xcb1 \
#         libatspi2.0-0 \
#         libsecret-1-0 \
#         libgtk-3-0 \
#     && rm -rf /var/lib/apt/lists/*


# 设置 Playwright 使用国内镜像源并安装浏览器(无头版本)及其系统依赖
# ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
# RUN poetry run playwright install chromium-headless-shell

# 这一层将被缓存，除非pyproject.toml或poetry.toml发生变化