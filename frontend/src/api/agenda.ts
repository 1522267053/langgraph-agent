/**
 * 日程管理 API
 */
import { get, post } from './index'
import type { PaginatedResponse, PaginationParams, ApiResponse } from '@/types/common'

export interface Agenda {
  id?: number
  title?: string
  description?: string
  start_time?: string
  end_time?: string
  category?: string
  priority?: number
  location?: string
  recurrence?: string
  status?: number
  completed_at?: string
  color?: string
  remind_at?: string
  is_reminded?: number
  creator_name?: string
  create_time?: string
  modify_time?: string
}

export interface AgendaCondition {
  title?: string
  category?: string
  status?: number
  creator_name?: string
  start_date?: string
  end_date?: string
}

export const agendaApi = {
  page(params: PaginationParams<AgendaCondition>) {
    return post<PaginatedResponse<Agenda>>('/agenda/page', params)
  },
  get(id: number) {
    return get<Agenda>(`/agenda/get/${id}`)
  },
  create(data: Partial<Agenda>) {
    return post<Agenda>('/agenda/create', data)
  },
  update(data: Partial<Agenda>) {
    return post<ApiResponse>('/agenda/update', data)
  },
  delete(id: number) {
    return get<ApiResponse>(`/agenda/delete/${id}`)
  },
  complete(id: number) {
    return post<Agenda>(`/agenda/complete/${id}`)
  },
  postpone(id: number) {
    return post<Agenda>(`/agenda/postpone/${id}`)
  },
  calendarEvents(start_date: string, end_date: string) {
    return get<Agenda[]>(`/agenda/calendar-events?start_date=${start_date}&end_date=${end_date}`)
  }
}
