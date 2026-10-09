"""
中国法定节假日数据服务

数据源 holiday-cn（NateScarlet/holiday-cn，MIT）：每日自动抓取国务院公告，
每年一个 {year}.json（{"year": 2026, "days": [{"name","date","isOffDay"}...]}）。

取代 chinesecalendar 常量包（数据编死在 wheel 里，每年需升级版本才能覆盖新年份）；
上游 JSON 每年国务院安排发布后自动更新，无需重装依赖。
国务院公告原文可溯源仓库 paper/{year}.md。
"""

import asyncio
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Optional

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)

# 按国内可达性排序（2026-10 实测：jsDelivr 0.7s / fastly 0.3s / github raw 超时）；
# raw 仅作兜底，个别网络环境可达
HOLIDAY_DATA_SOURCES = [
    "https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json",
    "https://fastly.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json",
    "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json",
]

# 磁盘缓存目录（相对项目根，与 sqlite 同在 data/ 下）
CACHE_DIR_RELATIVE = "data/holidays"
# 磁盘缓存保鲜期：超过则后台静默重拉（国务院 10-11 月发布次年安排）
CACHE_STALE_SECONDS = 7 * 24 * 3600
# 拉取失败负缓存：避免每次查询都反复打网络（风暴雨防护）
NEGATIVE_CACHE_SECONDS = 6 * 3600
# 单源超时（秒）
FETCH_TIMEOUT_SECONDS = 5


class HolidayStore:
    """holiday-cn 数据的三层缓存与节假日判定

    内存 dict → 磁盘 data/holidays/{year}.json → 在线多镜像拉取。
    is_holiday 为同步纯内存查询（agenda 扫描 3 年逐日调用，不能有 IO）；
    IO 全部收敛在 ensure_years（async），由调用方在扫描前预热。
    """

    def __init__(self) -> None:
        # {year: {date(ISO): isOffDay}}
        self._years: dict[int, dict[str, bool]] = {}
        # {year: 失败时间戳}：负缓存期内不重试网络
        self._failed_at: dict[int, float] = {}
        self._lock = asyncio.Lock()
        self._cache_dir: Optional[Path] = None

    # ---- 对外接口 ----

    def is_holiday(self, d: date) -> bool:
        """判定法定节假日（isOffDay=True）；调休补班日=False；无数据降级 False"""
        mapping = self._years.get(d.year)
        if mapping is None:
            logger.warning(
                "节假日数据未加载: year=%s（未预热或历史拉取失败），按非节假日处理",
                d.year,
            )
            return False
        return mapping.get(d.isoformat(), False)

    async def ensure_years(self, years: list[int]) -> None:
        """确保年份就绪：缺失且不在负缓存期的年份走磁盘→在线逐层补齐"""
        now = time.monotonic()
        missing = [
            y
            for y in years
            if y not in self._years
            and now - self._failed_at.get(y, float("-inf")) > NEGATIVE_CACHE_SECONDS
        ]
        if not missing:
            return

        async with self._lock:
            # 双重检查：等锁期间可能已被并发调用补齐
            now = time.monotonic()
            missing = [
                y
                for y in years
                if y not in self._years
                and now - self._failed_at.get(y, float("-inf")) > NEGATIVE_CACHE_SECONDS
            ]
            for year in missing:
                await self._load_year(year)

    # ---- 加载链路：磁盘 → 在线 ----

    async def _load_year(self, year: int) -> None:
        cached = self._load_from_disk(year)
        if cached is not None:
            self._years[year] = cached
            # 过期缓存先可用后保鲜：后台重拉不阻塞当前查询
            if self._disk_cache_is_stale(year):
                asyncio.create_task(self._refresh_from_network(year))
            return

        await self._refresh_from_network(year, negative_cache_on_fail=True)

    def _cache_path(self, year: int) -> Path:
        if self._cache_dir is None:
            self._cache_dir = Path(settings.get_absolute_path(CACHE_DIR_RELATIVE))
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        return self._cache_dir / f"{year}.json"

    def _load_from_disk(self, year: int) -> Optional[dict[str, bool]]:
        path = self._cache_path(year)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            mapping = self._validate_payload(payload, year)
            if mapping is None:
                logger.warning("节假日磁盘缓存损坏，将重新拉取: %s", path)
                return None
            return mapping
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("节假日磁盘缓存读取失败 %s: %s", path, e)
            return None

    def _disk_cache_is_stale(self, year: int) -> bool:
        try:
            mtime = self._cache_path(year).stat().st_mtime
            return time.time() - mtime > CACHE_STALE_SECONDS
        except OSError:
            return True

    async def _refresh_from_network(
        self, year: int, negative_cache_on_fail: bool = False
    ) -> None:
        """多镜像按序拉取；成功写内存+磁盘，失败按需写负缓存"""
        for source in HOLIDAY_DATA_SOURCES:
            url = source.format(year=year)
            try:
                async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    payload = resp.json()
                mapping = self._validate_payload(payload, year)
                if mapping is None:
                    logger.warning("节假日数据源结构异常，换下一镜像: %s", url)
                    continue
                self._years[year] = mapping
                self._failed_at.pop(year, None)
                self._write_disk_cache(year, payload)
                logger.info("节假日数据已加载: year=%s, source=%s", year, url)
                return
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("节假日数据拉取失败 %s: %s", url, e)
                continue

        if negative_cache_on_fail:
            self._failed_at[year] = time.monotonic()
        logger.warning(
            "节假日数据所有镜像均失败: year=%s（6小时内不重试，按非节假日降级）", year
        )

    def _write_disk_cache(self, year: int, payload: dict) -> None:
        try:
            self._cache_path(year).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:
            # 缓存写失败不影响内存可用性
            logger.warning("节假日磁盘缓存写入失败 year=%s: %s", year, e)

    @staticmethod
    def _validate_payload(payload: dict, year: int) -> Optional[dict[str, bool]]:
        """结构校验：必须含匹配的 year 与非空 days，防镜像污染写入坏缓存"""
        if not isinstance(payload, dict):
            return None
        if payload.get("year") != year:
            return None
        days = payload.get("days")
        if not isinstance(days, list) or not days:
            return None
        mapping: dict[str, bool] = {}
        for item in days:
            if not isinstance(item, dict):
                return None
            day = item.get("date")
            off = item.get("isOffDay")
            if not isinstance(day, str) or not isinstance(off, bool):
                return None
            try:
                date.fromisoformat(day)
            except ValueError:
                return None
            mapping[day] = off
        return mapping


def _prune_stale_disk_cache() -> None:
    """清理超过保鲜期 4 倍的磁盘缓存（进程启动时顺手执行，防无限累积）"""
    try:
        cache_dir = Path(settings.get_absolute_path(CACHE_DIR_RELATIVE))
        if not cache_dir.exists():
            return
        deadline = time.time() - CACHE_STALE_SECONDS * 4
        for path in cache_dir.glob("*.json"):
            if path.stat().st_mtime < deadline:
                path.unlink(missing_ok=True)
    except OSError:
        pass


_prune_stale_disk_cache()

holiday_service = HolidayStore()
