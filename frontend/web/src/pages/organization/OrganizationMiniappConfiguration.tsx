import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CheckCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import {
  Alert,
  App,
  Button,
  Descriptions,
  Form,
  Input,
  Skeleton,
  Space,
  Tag,
  Typography,
} from 'antd';
import {
  activateOrganizationMiniappConfiguration,
  changeOrganizationMiniappLogin,
  getOrganizationMiniappConfiguration,
  putOrganizationMiniappConfiguration,
  type DirectoryContext,
  type MiniappConfiguration,
  type PutMiniappConfigurationRequest,
} from '@/api/identityDirectory';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import { formatShanghaiTime } from '@/utils/decimal';

interface OrganizationMiniappConfigurationProps {
  active: boolean;
  context: DirectoryContext;
  organizationCode: string;
}

interface MiniappDraft {
  appId: string;
  displayName: string;
  appSecret: string;
}

const EMPTY_DRAFT: MiniappDraft = {
  appId: '',
  displayName: '',
  appSecret: '',
};

export default function OrganizationMiniappConfiguration({
  active,
  context,
  organizationCode,
}: OrganizationMiniappConfigurationProps) {
  const { message, modal } = App.useApp();
  const executeCommand = useCommandExecutor();
  const requestSequence = useRef(0);
  const [configuration, setConfiguration] =
    useState<MiniappConfiguration | null>(null);
  const [draft, setDraft] = useState<MiniappDraft>(EMPTY_DRAFT);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadConfiguration = useCallback(async () => {
    const sequence = ++requestSequence.current;
    setLoading(true);
    setLoadError(null);
    try {
      const loaded = await getOrganizationMiniappConfiguration(
        context,
        organizationCode,
      );
      if (requestSequence.current !== sequence) return;
      setConfiguration(loaded);
      setDraft({
        appId: loaded.appId,
        displayName: loaded.displayName,
        appSecret: '',
      });
    } catch (error) {
      if (requestSequence.current !== sequence) return;
      if (error instanceof ApiProblem && error.status === 404) {
        setConfiguration(null);
        setDraft(EMPTY_DRAFT);
      } else {
        setLoadError(
          error instanceof Error ? error.message : '小程序配置加载失败',
        );
      }
    } finally {
      if (requestSequence.current === sequence) setLoading(false);
    }
  }, [context, organizationCode]);

  useEffect(() => {
    if (active) {
      void loadConfiguration();
      return;
    }
    requestSequence.current += 1;
    setConfiguration(null);
    setDraft(EMPTY_DRAFT);
    setLoadError(null);
    setLoading(false);
    setBusy(false);
  }, [active, loadConfiguration]);

  const recoverFromConflict = async (error: unknown) => {
    if (error instanceof ApiProblem && error.isVersionConflict) {
      await loadConfiguration();
      message.warning('配置已被其他操作更新，请检查最新内容后重新确认');
    }
  };

  const saveConfiguration = async () => {
    const appId = draft.appId.trim();
    const displayName = draft.displayName.trim();
    if (!/^wx[0-9A-Za-z]{16}$/.test(appId)) {
      message.warning('AppID 应为 wx 开头的 18 位标识');
      return;
    }
    if (!displayName) {
      message.warning('请填写小程序展示名称');
      return;
    }
    if (!configuration?.appSecretConfigured && !draft.appSecret.trim()) {
      message.warning('当前没有有效 AppSecret，必须填写后才能保存');
      return;
    }
    if (draft.appSecret.length > 256) {
      message.warning('AppSecret 不能超过 256 个字符');
      return;
    }

    const payload: PutMiniappConfigurationRequest = {
      appId,
      displayName,
      expectedVersion: configuration?.version ?? null,
      ...(draft.appSecret ? { appSecret: draft.appSecret } : {}),
    };
    setBusy(true);
    try {
      await executeCommand(
        commandKey('put-miniapp-configuration', organizationCode, payload),
        (intent) =>
          putOrganizationMiniappConfiguration(
            context,
            organizationCode,
            payload,
            intent,
          ),
      );
      message.success(configuration ? '小程序配置已更新' : '小程序配置已创建');
      await loadConfiguration();
    } catch (error) {
      await recoverFromConflict(error);
    } finally {
      setBusy(false);
    }
  };

  const activateConfiguration = () => {
    if (!configuration || configuration.activated) return;
    modal.confirm({
      title: '激活这个 AppID？',
      content: (
        <Space direction="vertical" size={8}>
          <Typography.Text>
            激活后，机构将永久固定使用 AppID：
          </Typography.Text>
          <Typography.Text code copyable>
            {configuration.appId}
          </Typography.Text>
          <Typography.Text type="danger">
            激活后 AppID 不可修改，但仍可更新展示名称和轮换 AppSecret。
          </Typography.Text>
        </Space>
      ),
      okText: '确认激活',
      cancelText: '暂不激活',
      async onOk() {
        setBusy(true);
        try {
          const payload = {
            expectedVersion: configuration.version,
            reason: '激活机构小程序登录配置',
          };
          await executeCommand(
            commandKey(
              'activate-miniapp-configuration',
              organizationCode,
              payload,
            ),
            (intent) =>
              activateOrganizationMiniappConfiguration(
                context,
                organizationCode,
                configuration.version,
                intent,
                payload.reason,
              ),
          );
          message.success('AppID 已激活');
          await loadConfiguration();
        } catch (error) {
          await recoverFromConflict(error);
          throw error;
        } finally {
          setBusy(false);
        }
      },
    });
  };

  const toggleLogin = () => {
    if (!configuration) return;
    const enabled = !configuration.loginEnabled;
    modal.confirm({
      title: enabled ? '启用小程序身份登录？' : '停用小程序身份登录？',
      content: enabled
        ? '启用后，该机构的用户可以通过当前 AppID 建立小程序登录身份。'
        : '停用会立即撤销该机构当前有效的小程序会话，已登录用户需要重新登录。',
      okText: enabled ? '确认启用' : '确认停用',
      okButtonProps: { danger: !enabled },
      cancelText: '取消',
      async onOk() {
        setBusy(true);
        try {
          const payload = {
            enabled,
            expectedVersion: configuration.version,
            reason: enabled
              ? '启用机构小程序身份登录'
              : '停用机构小程序身份登录',
          };
          await executeCommand(
            commandKey('change-miniapp-login', organizationCode, payload),
            (intent) =>
              changeOrganizationMiniappLogin(
                context,
                organizationCode,
                enabled,
                configuration.version,
                intent,
                payload.reason,
              ),
          );
          message.success(enabled ? '小程序身份登录已启用' : '小程序身份登录已停用');
          await loadConfiguration();
        } catch (error) {
          await recoverFromConflict(error);
          throw error;
        } finally {
          setBusy(false);
        }
      },
    });
  };

  if (loading) {
    return <Skeleton active paragraph={{ rows: 5 }} />;
  }

  if (loadError) {
    return (
      <Alert
        type="error"
        showIcon
        message="暂时无法读取小程序配置"
        description={loadError}
        action={
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={() => void loadConfiguration()}
          >
            重试
          </Button>
        }
      />
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type={configuration?.appSecretConfigured ? 'info' : 'warning'}
        showIcon
        message={
          configuration
            ? configuration.appSecretConfigured
              ? 'AppSecret 仅在当前编辑窗口内存中短暂显示'
              : '当前 AppSecret 已失效，需要重新配置'
            : '尚未配置小程序身份登录'
        }
        description={
          configuration
            ? configuration.appSecretConfigured
              ? '更新配置时 AppSecret 留空即保持不变；不要将完整密钥复制到网址、日志或浏览器存储。'
              : '旧测试密钥已被清除；请填写新的 AppSecret 后保存，登录才能继续使用。'
            : '首次保存需要填写 AppID、展示名称和 AppSecret；保存后还需要激活 AppID 并单独启用登录。'
        }
      />

      {configuration && (
        <Descriptions size="small" column={2} bordered>
          <Descriptions.Item label="配置状态">
            <Space wrap>
              <Tag
                color={configuration.activated ? 'success' : 'warning'}
                icon={
                  configuration.activated ? <CheckCircleOutlined /> : undefined
                }
              >
                {configuration.activated ? 'AppID 已激活' : '待激活'}
              </Tag>
              <Tag color={configuration.loginEnabled ? 'processing' : 'default'}>
                {configuration.loginEnabled ? '登录已启用' : '登录已停用'}
              </Tag>
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="配置版本">
            {configuration.version}
          </Descriptions.Item>
          <Descriptions.Item label="最近更新" span={2}>
            {formatShanghaiTime(configuration.updatedAt)}
          </Descriptions.Item>
        </Descriptions>
      )}

      <Form.Item label="AppID" required>
        <Input
          value={draft.appId}
          disabled={configuration?.activated || busy}
          placeholder="wx1234567890abcdef"
          maxLength={18}
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              appId: event.target.value,
            }))
          }
        />
      </Form.Item>
      <Form.Item label="小程序展示名称" required>
        <Input
          value={draft.displayName}
          disabled={busy}
          placeholder="用于识别该机构对应的小程序"
          maxLength={100}
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              displayName: event.target.value,
            }))
          }
        />
      </Form.Item>
      {configuration?.appSecretConfigured && (
        <Form.Item
          label="当前 AppSecret"
          extra={`服务端脱敏值：${configuration.maskedAppSecret ?? '已配置'}`}
        >
          <Input.Password
            readOnly
            value={configuration.appSecret ?? ''}
            autoComplete="off"
            aria-label="当前完整 AppSecret"
          />
        </Form.Item>
      )}
      <Form.Item
        label={configuration?.appSecretConfigured ? '轮换 AppSecret' : 'AppSecret'}
        required={!configuration?.appSecretConfigured}
        extra={
          configuration?.appSecretConfigured
            ? '留空表示保持当前密钥不变；输入新值会立即轮换。'
            : '完整密钥将写入后端秘密设施，不写入业务数据库。'
        }
      >
        <Input.Password
          value={draft.appSecret}
          disabled={busy}
          autoComplete="new-password"
          maxLength={256}
          placeholder={
            configuration?.appSecretConfigured
              ? '如需轮换，请输入新的 AppSecret'
              : '请输入 AppSecret'
          }
          onChange={(event) =>
            setDraft((current) => ({
              ...current,
              appSecret: event.target.value,
            }))
          }
        />
      </Form.Item>

      <Space wrap>
        <Button
          type="primary"
          htmlType="button"
          loading={busy}
          icon={<SafetyCertificateOutlined />}
          onClick={() => void saveConfiguration()}
        >
          {configuration ? '保存配置' : '创建配置'}
        </Button>
        {configuration && !configuration.activated && (
          <Button
            htmlType="button"
            disabled={busy}
            onClick={activateConfiguration}
          >
            激活 AppID
          </Button>
        )}
        {configuration && (
          <Button
            htmlType="button"
            danger={configuration.loginEnabled}
            disabled={busy || (!configuration.activated && !configuration.loginEnabled)}
            onClick={toggleLogin}
          >
            {configuration.loginEnabled ? '停用身份登录' : '启用身份登录'}
          </Button>
        )}
      </Space>
      {configuration && !configuration.activated && (
        <Typography.Text type="secondary">
          激活仅锁定本地 AppID 配置，不代表微信平台已经完成校验。
        </Typography.Text>
      )}
    </Space>
  );
}
