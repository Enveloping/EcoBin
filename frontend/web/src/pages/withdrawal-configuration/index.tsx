import { useCallback, useEffect, useRef, useState } from 'react';
import { ReloadOutlined, WalletOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
} from 'antd';
import {
  getWithdrawalConfiguration,
  releaseWithdrawalConfiguration,
  type ReleaseWithdrawalConfigurationRequest,
  type WithdrawalConfiguration,
} from '@/api/funds';
import { ApiProblem } from '@/api/request';
import { commandKey, useCommandExecutor } from '@/hooks/useCommandExecutor';
import DirectoryScopeBar from '@/pages/identity/DirectoryScopeBar';
import { useDirectoryScope } from '@/pages/identity/useDirectoryScope';
import { useOrganizationScope } from '@/pages/identity/useOrganizationScope';
import { useAuthStore } from '@/stores/authStore';
import { formatShanghaiTime } from '@/utils/decimal';
import { pageHeader } from '@/utils/pageStyle';

const MONEY_PATTERN = /^(0|[1-9][0-9]*)\.[0-9]{2}$/;

interface WithdrawalConfigurationForm {
  hardLimitYuan: string;
  manualMinimumYuan: string;
  manualMaximumYuan: string;
  manualReviewFreeThresholdYuan: string;
  autoWithdrawalEnabled: boolean;
  autoMinimumYuan?: string;
  autoMaximumYuan?: string;
  autoReviewFreeThresholdYuan?: string;
}

function errorText(error: unknown): string {
  if (error instanceof ApiProblem) {
    return error.requestId
      ? `${error.message}（请求 ID：${error.requestId}）`
      : error.message;
  }
  return error instanceof Error ? error.message : '提现规则加载失败';
}

export default function WithdrawalConfigurationPage() {
  const directory = useDirectoryScope();
  const organization = useOrganizationScope(directory);
  const session = useAuthStore((state) => state.session);
  const executeCommand = useCommandExecutor();
  const { message } = App.useApp();
  const loadSequence = useRef(0);
  const [form] = Form.useForm<WithdrawalConfigurationForm>();
  const automaticWithdrawalEnabled = Form.useWatch(
    'autoWithdrawalEnabled',
    form,
  );
  const [configuration, setConfiguration] =
    useState<WithdrawalConfiguration | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string>();
  const [releaseOpen, setReleaseOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const mayManage = directory.context?.domain !== 'platform'
    && !!session?.capabilities.includes('withdrawal.configuration.manage');

  const load = useCallback(async () => {
    if (!directory.context || !organization.organizationCode) {
      setConfiguration(null);
      setLoadError(undefined);
      return;
    }
    const sequence = ++loadSequence.current;
    setLoading(true);
    setLoadError(undefined);
    try {
      const loaded = await getWithdrawalConfiguration(
        directory.context,
        organization.organizationCode,
      );
      if (loadSequence.current !== sequence) return;
      setConfiguration(loaded);
    } catch (error) {
      if (loadSequence.current !== sequence) return;
      setConfiguration(null);
      setLoadError(errorText(error));
    } finally {
      if (loadSequence.current === sequence) setLoading(false);
    }
  }, [directory.context, organization.organizationCode]);

  useEffect(() => {
    void load();
    return () => {
      loadSequence.current += 1;
    };
  }, [load]);

  const openRelease = () => {
    if (!configuration) return;
    form.setFieldsValue({
      hardLimitYuan: configuration.hardLimitYuan,
      manualMinimumYuan: configuration.manualMinimumYuan,
      manualMaximumYuan: configuration.manualMaximumYuan,
      manualReviewFreeThresholdYuan:
        configuration.manualReviewFreeThresholdYuan,
      autoWithdrawalEnabled: configuration.autoWithdrawalEnabled,
      autoMinimumYuan: configuration.autoMinimumYuan ?? undefined,
      autoMaximumYuan: configuration.autoMaximumYuan ?? undefined,
      autoReviewFreeThresholdYuan:
        configuration.autoReviewFreeThresholdYuan ?? undefined,
    });
    setReleaseOpen(true);
  };

  const release = async () => {
    if (
      !configuration
      || !directory.context
      || !organization.organizationCode
      || !mayManage
    ) return;
    let values: WithdrawalConfigurationForm;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const enabled = values.autoWithdrawalEnabled;
    const payload: ReleaseWithdrawalConfigurationRequest = {
      expectedCurrentVersion: configuration.versionNo,
      hardLimitYuan: values.hardLimitYuan.trim(),
      manualMinimumYuan: values.manualMinimumYuan.trim(),
      manualMaximumYuan: values.manualMaximumYuan.trim(),
      manualReviewFreeThresholdYuan:
        values.manualReviewFreeThresholdYuan.trim(),
      autoWithdrawalEnabled: enabled,
      autoMinimumYuan: enabled ? values.autoMinimumYuan?.trim() : null,
      autoMaximumYuan: enabled ? values.autoMaximumYuan?.trim() : null,
      autoReviewFreeThresholdYuan: enabled
        ? values.autoReviewFreeThresholdYuan?.trim()
        : null,
    };

    setSubmitting(true);
    try {
      await executeCommand(
        commandKey(
          'release-withdrawal-configuration',
          organization.organizationCode,
          payload,
        ),
        (intent) => releaseWithdrawalConfiguration(
          directory.context!,
          organization.organizationCode!,
          payload,
          intent,
        ),
      );
      setReleaseOpen(false);
      message.success('提现规则新版本已发布');
      await load();
    } catch (error) {
      message.error(errorText(error));
      if (error instanceof ApiProblem && error.isVersionConflict) {
        await load();
      }
    } finally {
      setSubmitting(false);
    }
  };

  const content = (() => {
    if (directory.loading || organization.loading) {
      return <Card><Spin tip="正在确定配置作用范围" /></Card>;
    }
    if (!directory.context) return <Empty description="请选择目标租户" />;
    if (!organization.organizationOptions.length) {
      return <Empty description="当前租户尚无机构" />;
    }
    if (!organization.organizationCode) return <Spin />;

    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card size="small">
          <Space wrap>
            <Typography.Text strong>目标机构</Typography.Text>
            <Select
              aria-label="目标机构"
              showSearch
              optionFilterProp="label"
              style={{ width: 360 }}
              value={organization.organizationCode}
              options={organization.organizationOptions}
              onChange={organization.setOrganizationCode}
            />
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新规则
            </Button>
          </Space>
        </Card>

        {directory.platform && (
          <Alert
            showIcon
            type="info"
            message="平台管理员只读查看"
            description="提现规则属于机构资金决策，只能由目标租户内具有提现规则管理权限的工作人员发布。"
          />
        )}
        {loadError && (
          <Alert
            showIcon
            type="error"
            message="暂时无法读取提现规则"
            description={loadError}
            action={<Button size="small" onClick={() => void load()}>重试</Button>}
          />
        )}
        {loading ? (
          <Card><Spin tip="正在读取当前提现规则" /></Card>
        ) : configuration && (
          <Card
            title={<Space><WalletOutlined />当前提现规则<Tag color="blue">v{configuration.versionNo}</Tag></Space>}
            extra={mayManage && (
              <Button type="primary" onClick={openRelease}>发布新版本</Button>
            )}
          >
            <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
              <Descriptions.Item label="单次最大提现金额">
                ¥ {configuration.hardLimitYuan}
              </Descriptions.Item>
              <Descriptions.Item label="手动提现范围">
                ¥ {configuration.manualMinimumYuan} — ¥ {configuration.manualMaximumYuan}
              </Descriptions.Item>
              <Descriptions.Item label="手动提现免审阈值">
                不超过 ¥ {configuration.manualReviewFreeThresholdYuan}
              </Descriptions.Item>
              <Descriptions.Item label="投递返现自动提现">
                <Tag color={configuration.autoWithdrawalEnabled ? 'success' : 'default'}>
                  {configuration.autoWithdrawalEnabled ? '已启用' : '未启用'}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="自动提现范围">
                {configuration.autoWithdrawalEnabled
                  ? `¥ ${configuration.autoMinimumYuan} — ¥ ${configuration.autoMaximumYuan}`
                  : '不适用'}
              </Descriptions.Item>
              <Descriptions.Item label="自动提现免审阈值">
                {configuration.autoWithdrawalEnabled
                  ? `不超过 ¥ ${configuration.autoReviewFreeThresholdYuan}`
                  : '不适用'}
              </Descriptions.Item>
              <Descriptions.Item label="发布时间">
                {formatShanghaiTime(configuration.publishedAt)}
              </Descriptions.Item>
            </Descriptions>
          </Card>
        )}
      </Space>
    );
  })();

  return (
    <PageContainer
      {...pageHeader(
        '提现规则',
        '统一设置手动提现、投递返现自动提现及共用的单次最大金额。',
      )}
    >
      <DirectoryScopeBar scope={directory} />
      {content}

      <Modal
        title="发布新的提现规则"
        open={releaseOpen}
        width={680}
        okText="确认发布"
        confirmLoading={submitting}
        onOk={() => void release()}
        onCancel={() => !submitting && setReleaseOpen(false)}
        destroyOnClose
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 18 }}
          message="规则按版本冻结，发布后不覆盖历史提现"
          description="自动提现只处理本次投递首次审核产生的正返现。不满足授权、余额或金额范围等条件时，本次安全跳过，不会事后补建。"
        />
        <Form<WithdrawalConfigurationForm>
          form={form}
          layout="vertical"
          disabled={submitting}
        >
          <MoneyField
            name="hardLimitYuan"
            label="单次最大提现金额（元）"
            extra="手动和自动提现都不能超过该值；当前系统硬约束最高为 200.00 元。"
          />
          <Row gutter={16}>
            <Col span={12}>
              <MoneyField name="manualMinimumYuan" label="手动提现最低额（元）" />
            </Col>
            <Col span={12}>
              <MoneyField name="manualMaximumYuan" label="手动提现最高额（元）" />
            </Col>
          </Row>
          <MoneyField
            name="manualReviewFreeThresholdYuan"
            label="手动提现免人工审核阈值（元）"
            extra="金额小于或等于该值时，系统直接批准；填写 0.00 表示所有正金额都需要人工审核。"
          />
          <Form.Item
            name="autoWithdrawalEnabled"
            label="投递返现自动提现"
            valuePropName="checked"
          >
            <Switch checkedChildren="启用" unCheckedChildren="停用" />
          </Form.Item>
          {automaticWithdrawalEnabled && (
            <>
              <Row gutter={16}>
                <Col span={12}>
                  <MoneyField name="autoMinimumYuan" label="自动提现最低额（元）" />
                </Col>
                <Col span={12}>
                  <MoneyField name="autoMaximumYuan" label="自动提现最高额（元）" />
                </Col>
              </Row>
              <MoneyField
                name="autoReviewFreeThresholdYuan"
                label="自动提现免人工审核阈值（元）"
                extra="自动创建的提现不超过该值时，系统直接批准并提交微信；超过后仍需工作人员审核。"
              />
            </>
          )}
        </Form>
      </Modal>
    </PageContainer>
  );
}

function MoneyField({
  name,
  label,
  extra,
}: {
  name: keyof WithdrawalConfigurationForm;
  label: string;
  extra?: string;
}) {
  return (
    <Form.Item
      name={name}
      label={label}
      extra={extra}
      rules={[
        { required: true, message: `请输入${label}` },
        {
          pattern: MONEY_PATTERN,
          message: '请输入非负金额并精确到分，例如 1.00 或 0.00',
        },
      ]}
    >
      <Input placeholder="0.00" />
    </Form.Item>
  );
}
