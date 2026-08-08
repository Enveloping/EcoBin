const INVALID_PERCENT_ESCAPE = /%(?![0-9A-Fa-f]{2})/;
const EXPLICIT_HTTPS_AUTHORITY = /^https:\/\/[^/?#]/i;

/** 与服务端一致：平台只保存尚未携带 deviceCode 的普通 HTTPS 入口。 */
export function isDeviceEntryBaseUrl(value: string): boolean {
  if (
    !value
    || value !== value.trim()
    || !EXPLICIT_HTTPS_AUTHORITY.test(value)
    || INVALID_PERCENT_ESCAPE.test(value)
  ) {
    return false;
  }
  try {
    const url = new URL(value);
    return url.protocol === 'https:'
      && !!url.hostname
      && !url.username
      && !url.password
      && !url.hash
      && !url.searchParams.has('deviceCode');
  } catch {
    return false;
  }
}
