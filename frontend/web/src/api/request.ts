import axios, { type AxiosRequestConfig, type AxiosResponse } from 'axios';
import { message } from 'antd';
import type { Result } from '@/types';
import { authActions } from '@/stores/authStore';

const instance = axios.create({
  baseURL: (import.meta.env.VITE_API_BASE || '') + '/api',
  timeout: 15000,
});

// 请求拦截器：注入 Bearer token
instance.interceptors.request.use((config) => {
  const token = authActions.getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let redirecting = false;

// 响应拦截器：解包 Result<T>，统一错误处理
instance.interceptors.response.use(
  (response: AxiosResponse<Result>) => {
    const res = response.data;
    if (res.code === 200) {
      // 直接返回 data，业务层拿到的就是 T
      return res.data as unknown as AxiosResponse;
    }
    if (res.code === 401) {
      handleUnauthorized();
      return Promise.reject(new Error(res.message || '登录失效'));
    }
    if (res.code === 403) {
      message.error(res.message || '无权限访问');
      return Promise.reject(new Error(res.message || '无权限访问'));
    }
    // 其它业务异常：统一弹 message
    message.error(res.message || '请求失败');
    return Promise.reject(new Error(res.message || '请求失败'));
  },
  (error) => {
    // HTTP 层错误（网络/超时/非 2xx）
    const status = error?.response?.status;
    if (status === 401) {
      handleUnauthorized();
    } else {
      const msg =
        error?.response?.data?.message || error?.message || '网络异常，请稍后重试';
      message.error(msg);
    }
    return Promise.reject(error);
  },
);

function handleUnauthorized() {
  message.error('登录失效，请重新登录');
  authActions.clear();
  if (!redirecting && location.pathname !== '/login') {
    redirecting = true;
    setTimeout(() => {
      location.href = '/login';
      redirecting = false;
    }, 300);
  }
}

/** 泛型请求：resolve 出的就是后端 Result.data（即 T） */
export function request<T>(config: AxiosRequestConfig): Promise<T> {
  return instance.request<T, T>(config);
}

export default request;
