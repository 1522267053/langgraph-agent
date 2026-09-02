/**
 * 文件系统API
 * @description 浏览本地目录，供工作路径选择器使用
 */
import { get } from './index'

export interface DirectoryEntry {
  /** 目录名 */
  name: string
  /** 目录绝对路径 */
  path: string
}

export interface DirectoryListData {
  /** 当前目录绝对路径（盘符列表时为空） */
  path: string | null
  /** 上级目录路径（根/盘符列表时为空） */
  parent: string | null
  /** 子目录列表 */
  directories: DirectoryEntry[]
}

export const fsApi = {
  /**
   * 列出指定路径下的子目录
   * @param path 目录路径，空则返回 Windows 盘符列表
   */
  listDirectories(path?: string) {
    return get<DirectoryListData>('/fs/directories', { path })
  }
}
