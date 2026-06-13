import { post } from '@/api/index'
import type { ApiResponse } from '@/types/common'
import type {
  TokenStatisticsQuery,
  TokenOverview,
  TokenTrendItem,
  TokenByFlowItem,
  TokenByModelItem
} from '@/types/statistics'

export const statisticsApi = {
  tokenOverview(query?: TokenStatisticsQuery) {
    return post<ApiResponse<TokenOverview>>('/statistics/token-overview', query)
  },
  tokenTrend(query?: TokenStatisticsQuery) {
    return post<ApiResponse<TokenTrendItem[]>>('/statistics/token-trend', query)
  },
  tokenByFlow(query?: TokenStatisticsQuery) {
    return post<ApiResponse<TokenByFlowItem[]>>('/statistics/token-by-flow', query)
  },
  tokenByModel(query?: TokenStatisticsQuery) {
    return post<ApiResponse<TokenByModelItem[]>>('/statistics/token-by-model', query)
  }
}
