/**
 * 列表页面通用逻辑 Hook
 * @description 封装列表页面的通用功能：分页、搜索、选择等
 */

import { ref, computed, watch, type Ref, type ComputedRef } from 'vue'

/** 分页参数 */
export interface PaginationState {
  /** 当前页码 */
  page: number
  /** 每页条数 */
  pageSize: number
  /** 总记录数 */
  total: number
}

/** 列表页面选项 */
export interface UseListPageOptions<T, Q> {
  /** 数据加载函数 */
  fetchData: (params: { page: number; pageSize: number; condition?: Q }) => Promise<{
    items: T[]
    total: number
  }>
  /** 查询条件 */
  condition?: Ref<Q>
  /** 是否立即加载 */
  immediate?: boolean
  /** 默认每页条数 */
  defaultPageSize?: number
}

/** 列表页面返回值 */
export interface UseListPageReturn<T> {
  /** 数据列表 */
  data: Ref<T[]>
  /** 加载状态 */
  loading: Ref<boolean>
  /** 分页状态 */
  pagination: PaginationState
  /** 是否有数据 */
  hasData: ComputedRef<boolean>
  /** 是否为空 */
  isEmpty: ComputedRef<boolean>
  /** 选中的行 */
  selectedRows: Ref<T[]>
  /** 加载数据 */
  loadData: () => Promise<void>
  /** 刷新数据（保持当前页） */
  refresh: () => Promise<void>
  /** 重置并加载第一页 */
  resetAndLoad: () => Promise<void>
  /** 页码改变 */
  onPageChange: (page: number) => void
  /** 每页条数改变 */
  onPageSizeChange: (pageSize: number) => void
  /** 选择改变 */
  onSelectionChange: (rows: T[]) => void
  /** 清空选择 */
  clearSelection: () => void
}

/**
 * 列表页面通用逻辑 Hook
 * @description 封装列表页面的分页、搜索、加载等通用逻辑
 * @param options 配置选项
 * @returns 列表页面状态和方法
 */
export function useListPage<T, Q = Record<string, unknown>>(
  options: UseListPageOptions<T, Q>
): UseListPageReturn<T> {
  const { fetchData, condition, immediate = true, defaultPageSize = 10 } = options

  // 数据状态
  const data = ref<T[]>([]) as Ref<T[]>
  const loading = ref(false)

  // 分页状态
  const pagination = ref<PaginationState>({
    page: 1,
    pageSize: defaultPageSize,
    total: 0
  })

  // 选中状态
  const selectedRows = ref<T[]>([]) as Ref<T[]>

  // 计算属性
  const hasData = computed(() => data.value.length > 0)
  const isEmpty = computed(() => !loading.value && data.value.length === 0)

  /**
   * 加载数据
   */
  async function loadData(): Promise<void> {
    loading.value = true
    try {
      const result = await fetchData({
        page: pagination.value.page,
        pageSize: pagination.value.pageSize,
        condition: condition?.value
      })
      data.value = result.items
      pagination.value.total = result.total
    } catch (error) {
      console.error('[useListPage] Failed to load data:', error)
      data.value = []
      pagination.value.total = 0
    } finally {
      loading.value = false
    }
  }

  /**
   * 刷新数据
   */
  async function refresh(): Promise<void> {
    await loadData()
  }

  /**
   * 重置分页并加载第一页
   */
  async function resetAndLoad(): Promise<void> {
    pagination.value.page = 1
    await loadData()
  }

  /**
   * 页码改变处理
   */
  function onPageChange(page: number): void {
    pagination.value.page = page
    loadData()
  }

  /**
   * 每页条数改变处理
   */
  function onPageSizeChange(pageSize: number): void {
    pagination.value.pageSize = pageSize
    pagination.value.page = 1
    loadData()
  }

  /**
   * 选择改变处理
   */
  function onSelectionChange(rows: T[]): void {
    selectedRows.value = rows
  }

  /**
   * 清空选择
   */
  function clearSelection(): void {
    selectedRows.value = []
  }

  // 监听查询条件变化
  if (condition) {
    watch(
      condition,
      () => {
        resetAndLoad()
      },
      { deep: true }
    )
  }

  // 立即加载
  if (immediate) {
    loadData()
  }

  return {
    data,
    loading,
    pagination: pagination.value,
    hasData,
    isEmpty,
    selectedRows,
    loadData,
    refresh,
    resetAndLoad,
    onPageChange,
    onPageSizeChange,
    onSelectionChange,
    clearSelection
  }
}

/**
 * 简单列表状态 Hook
 * @description 不包含分页的简单列表状态管理
 */
export function useSimpleList<T>(options: { fetchData: () => Promise<T[]>; immediate?: boolean }) {
  const { fetchData, immediate = true } = options

  const data = ref<T[]>([]) as Ref<T[]>
  const loading = ref(false)

  async function load(): Promise<void> {
    loading.value = true
    try {
      data.value = await fetchData()
    } catch (error) {
      console.error('[useSimpleList] Failed to load data:', error)
      data.value = []
    } finally {
      loading.value = false
    }
  }

  if (immediate) {
    load()
  }

  return {
    data,
    loading,
    load,
    refresh: load
  }
}
