import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';
import { defaultPathFor } from '@/router/routes';
import { useAuthStore } from '@/stores/authStore';

export default function NotFoundPage() {
  const navigate = useNavigate();
  const session = useAuthStore((state) => state.session);

  return (
    <Result
      status="404"
      title="页面不存在"
      subTitle="这个地址没有可用页面，或对应功能尚未接入当前机器契约。"
      extra={
        <Button
          type="primary"
          onClick={() => navigate(defaultPathFor(session), { replace: true })}
        >
          返回可用首页
        </Button>
      }
    />
  );
}
