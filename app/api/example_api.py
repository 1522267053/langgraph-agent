"""
样例 API 路由
处理样例相关的路由定义
"""

from app.api.base_api import BaseApi
from app.models.example import Example
from app.services.example_serveice import example_service
from app.schemas.example_schema import ExampleBase, ExampleCreate, ExampleUpdate


class ExampleApi(
    BaseApi[Example, ExampleBase, ExampleBase, ExampleCreate, ExampleUpdate]
):
    """样例 API"""

    def __init__(self):
        super().__init__(
            service=example_service, router_prefix="/api/example", router_tags=["例子"]
        )


example_api = ExampleApi()
router = example_api.router
