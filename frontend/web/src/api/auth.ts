import request from './request';
import type { LoginRequest, LoginResponse } from '@/types';

export function login(data: LoginRequest) {
  return request<LoginResponse>({
    url: '/system/auth/login',
    method: 'POST',
    data,
  });
}
