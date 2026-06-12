"""
配置管理模块
使用 pydantic-settings 管理应用配置
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field

from app.config.build_utils import BASE_DIR, get_env_file


class Settings(BaseSettings):
    """应用配置类"""

    # 数据库配置
    database_type: str = Field(
        default="sqlite", alias="DATABASE_TYPE"
    )  # sqlite / mysql
    custom_database_url: str = Field(
        default="", alias="DATABASE_URL"
    )  # 直接指定完整 URL（优先级最高）
    sqlite_db_path: str = Field(
        default="data/langgraph_agent.db", alias="SQLITE_DB_PATH"
    )  # SQLite 文件路径（相对于项目根目录）
    database_host: str = Field(default="localhost", alias="DATABASE_HOST")
    database_port: int = Field(default=3306, alias="DATABASE_PORT")
    database_user: str = Field(default="root", alias="DATABASE_USER")
    database_password: str = Field(default="", alias="DATABASE_PASSWORD")
    database_name: str = Field(default="langgraph_agent", alias="DATABASE_NAME")
    database_pool_size: int = Field(
        default=50, alias="DATABASE_POOL_SIZE"
    )  # 连接池大小（建议 >= AI_MATCH_MAX_WORKERS + 10）
    database_max_overflow: int = Field(
        default=30, alias="DATABASE_MAX_OVERFLOW"
    )  # 连接池溢出大小（总连接数 = POOL_SIZE + MAX_OVERFLOW）

    # 应用配置
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    debug: bool = Field(default=True, alias="DEBUG")

    cors_origins: list[str] = Field(
        default=[
            "http://localhost:5173",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
        ],
        alias="CORS_ORIGINS",
    )

    # 文件上传配置
    upload_dir: str = Field(default="uploads", alias="UPLOAD_DIR")
    max_upload_size: int = Field(default=100, alias="MAX_UPLOAD_SIZE")

    # 向量数据库配置
    vector_store_type: str = Field(default="chroma", alias="VECTOR_STORE_TYPE")
    vector_store_path: str = Field(default="data/chroma", alias="VECTOR_STORE_PATH")
    vector_collection_name: str = Field(
        default="knowledge_segments", alias="VECTOR_COLLECTION_NAME"
    )

    # Embedding 配置（OpenAI 兼容接口）
    embedding_api_key: str = Field(default="", alias="EMBEDDING_API_KEY")
    embedding_base_url: str = Field(default="", alias="EMBEDDING_BASE_URL")
    embedding_model: str = Field(default="text-embedding-v3", alias="EMBEDDING_MODEL")
    embedding_batch_size: int = Field(default=10, alias="EMBEDDING_BATCH_SIZE")

    # 文档处理定时任务配置
    doc_process_interval: int = Field(
        default=60, alias="DOC_PROCESS_INTERVAL"
    )  # 文档处理轮询间隔（秒）
    doc_process_batch_size: int = Field(
        default=5, alias="DOC_PROCESS_BATCH_SIZE"
    )  # 每次最多处理的文档数

    # 登录密码配置（可选，为空则不启用登录保护）
    login_password: str = Field(default="", alias="LOGIN_PASSWORD")

    # 工具输出截断配置
    tool_output_max_lines: int = Field(
        default=500, alias="TOOL_OUTPUT_MAX_LINES"
    )  # 工具输出最大行数（超过时截断并保存到临时文件）
    tool_output_max_bytes: int = Field(
        default=10240, alias="TOOL_OUTPUT_MAX_BYTES"
    )  # 工具输出最大字节数（默认 10KB）
    tool_output_preview_lines: int = Field(
        default=50, alias="TOOL_OUTPUT_PREVIEW_LINES"
    )  # 截断后预览保留的行数
    tool_output_preview_bytes: int = Field(
        default=5120, alias="TOOL_OUTPUT_PREVIEW_BYTES"
    )  # 截断后预览保留的字节数（默认 5KB）

    # 资源市场服务器地址
    marketplace_server_url: str = Field(default="", alias="MARKETPLACE_SERVER_URL")

    # 版本更新检查接口地址
    version_check_url: str = Field(default="", alias="VERSION_CHECK_URL")

    # 日志配置
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_dir: str = Field(default="logs", alias="LOG_DIR")
    log_max_bytes: int = Field(default=10 * 1024 * 1024, alias="LOG_MAX_BYTES")  # 10MB
    log_backup_days: int = Field(default=7, alias="LOG_BACKUP_DAYS")
    log_format: str = Field(
        default="[%(asctime)s] [%(name)s] [%(levelname)s] [%(filename)s:%(lineno)d] - %(message)s",
        alias="LOG_FORMAT",
    )
    log_date_format: str = Field(default="%Y-%m-%d %H:%M:%S", alias="LOG_DATE_FORMAT")
    uvicorn_log_format: str = Field(
        default="[%(asctime)s] [%(name)s] [%(levelname)s] - %(message)s",
        alias="UVICORN_LOG_FORMAT",
    )
    uvicorn_access_log_format: str = Field(
        default='%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        alias="UVICORN_ACCESS_LOG_FORMAT",
    )

    @property
    def database_url(self) -> str:
        """构建数据库连接URL"""
        # 优先使用直接指定的 URL
        if self.custom_database_url:
            return self.custom_database_url
        # 根据数据库类型构建 URL
        if self.database_type == "sqlite":
            db_path = self.get_absolute_path(self.sqlite_db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite+aiosqlite:///{db_path}"
        return (
            f"mysql+aiomysql://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}?charset=utf8mb4"
        )

    @property
    def is_sqlite(self) -> bool:
        """当前是否使用 SQLite"""
        return (
            self.database_type == "sqlite"
            and not self.custom_database_url.startswith("mysql")
        )

    def get_absolute_path(self, relative_path: str) -> Path:
        """
        将相对路径转换为绝对路径

        Args:
            relative_path: 相对于项目根目录的路径

        Returns:
            Path: 绝对路径，如果路径为空则返回 None
        """
        if not relative_path:
            return None

        path = Path(relative_path)
        # 如果已经是绝对路径，直接返回
        if path.is_absolute():
            return path
        # 否则，相对于项目根目录进行解析
        return BASE_DIR / path

    class Config:
        env_file = str(get_env_file())
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


# 创建全局配置实例
settings = Settings()
