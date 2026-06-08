import request from './request';

type StatMap = Record<string, unknown>;

export function statDashboard() {
  return request<StatMap>({ url: '/statistics/dashboard', method: 'GET' });
}

export function statDevices() {
  return request<StatMap>({ url: '/statistics/devices', method: 'GET' });
}

export function statMembers() {
  return request<StatMap>({ url: '/statistics/members', method: 'GET' });
}

export function statDelivery() {
  return request<StatMap>({ url: '/statistics/delivery', method: 'GET' });
}

export function statClean() {
  return request<StatMap>({ url: '/statistics/clean', method: 'GET' });
}

export function statPayout() {
  return request<StatMap>({ url: '/statistics/payout', method: 'GET' });
}

export function statMemberMoney() {
  return request<StatMap>({ url: '/statistics/member-money', method: 'GET' });
}

export function statDevicesMap() {
  return request<Array<Record<string, unknown>>>({
    url: '/statistics/devices-map',
    method: 'GET',
  });
}

export function statDeviceRanking(pageSize = 5) {
  return request<Array<Record<string, unknown>>>({
    url: '/statistics/device-ranking',
    method: 'GET',
    params: { pageSize },
  });
}
