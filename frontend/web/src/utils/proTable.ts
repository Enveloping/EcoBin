import type { PageParams, PageResult } from '@/types';

/** ProTable params 形态（current/pageSize + 其它查询字段） */
export interface ProTableParams {
  current?: number;
  pageSize?: number;
  [key: string]: unknown;
}

export interface ProTableResult<T> {
  data: T[];
  total: number;
  success: boolean;
}

/**
 * 把后端分页函数适配成 ProTable 的 request 返回。
 * 自动把 ProTable 的 current → 后端 page，并剥离 ProTable 内部字段。
 */
export async function toProTableResult<T>(
  fetcher: (params: PageParams) => Promise<PageResult<T>>,
  params: ProTableParams,
): Promise<ProTableResult<T>> {
  const { current, pageSize, ...rest } = params;
  try {
    const res = await fetcher({ page: current ?? 1, pageSize: pageSize ?? 20, ...rest });
    return { data: res.records ?? [], total: res.total ?? 0, success: true };
  } catch {
    // 错误已由 axios 拦截器统一弹窗
    return { data: [], total: 0, success: false };
  }
}

/**
 * 适配「后端返回纯数组、无服务端分页」的接口（如 admin/tenant 列表）。
 * 拉全量后按 ProTable 的 current/pageSize 做客户端分页，保留分页 UI。
 */
export async function toProTableListResult<T>(
  fetcher: () => Promise<T[]>,
  params: ProTableParams,
): Promise<ProTableResult<T>> {
  const current = params.current ?? 1;
  const pageSize = params.pageSize ?? 20;
  try {
    const all = (await fetcher()) ?? [];
    const start = (current - 1) * pageSize;
    return { data: all.slice(start, start + pageSize), total: all.length, success: true };
  } catch {
    return { data: [], total: 0, success: false };
  }
}
