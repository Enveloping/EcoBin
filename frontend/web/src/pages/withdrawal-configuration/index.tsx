import HelpTip from '@/components/HelpTip';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ReloadOutlined, WalletOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  Row,
  Select,
  Space,
  Spin,
  Switch,
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
  return error instanceof Error ? error.message : '提现审核规则加载失败';
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
      form.setFieldsValue({
        hardLimitYuan: loaded.hardLimitYuan,
        manualMinimumYuan: loaded.manualMinimumYuan,
        manualMaximumYuan: loaded.manualMaximumYuan,
        manualReviewFreeThresholdYuan:
          loaded.manualReviewFreeThresholdYuan,
        autoWithdrawalEnabled: loaded.autoWithdrawalEnabled,
        autoMinimumYuan: loaded.autoMinimumYuan ?? undefined,
        autoMaximumYuan: loaded.autoMaximumYuan ?? undefined,
        autoReviewFreeThresholdYuan:
          loaded.autoReviewFreeThresholdYuan ?? undefined,
      });
    } catch (error) {
      if (loadSequence.current !== sequence) return;
      setConfiguration(null);
      setLoadError(errorText(error));
    } finally {
      if (loadSequence.current === sequence) setLoading(false);
    }
  }, [directory.context, form, organization.organizationCode]);

  useEffect(() => {
    void load();
    return () => {
      loadSequence.current += 1;
    };
  }, [load]);

  const save = async (values: WithdrawalConfigurationForm) => {
    if (
      !configuration
      || !directory.context
      || !organization.organizationCode
      || !mayManage
    ) return;
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
      message.success('提现审核规则已保存');
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
          <Typography.Text type="secondary">只读<HelpTip label="只读权限">规则由该租户内有权限的工作人员修改。</HelpTip></Typography.Text>
        )}
        {loadError && (
          <Alert
            showIcon
            type="error"
            message="暂时无法读取提现审核规则"
            description={loadError}
            action={<Button size="small" onClick={() => void load()}>重试</Button>}
          />
        )}
        {loading ? (
          <Card><Spin tip="正在读取当前提现审核规则" /></Card>
        ) : configuration && (
          <Card title={<Space><WalletOutlined />提现审核规则设置</Space>}>
            <Form<WithdrawalConfigurationForm>
              form={form}
              layout="vertical"
              disabled={!mayManage || submitting}
              onFinish={(values) => void save(values)}
            >
              <Space direction="vertical" size={24} style={{ width: '100%' }}>
                <section
                  aria-labelledby="withdrawal-common-title"
                  style={{ width: '100%' }}
                >
                  <Typography.Title
                    id="withdrawal-common-title"
                    level={5}
                    style={{ marginTop: 0, marginBottom: 4 }}
                  >
                    共同金额限制
                  </Typography.Title>
                  <MoneyField
                    name="hardLimitYuan"
                    label="单次最大提现金额（元）"
                    extra="适用于全部提现，最高 200.00 元。"
                  />
                </section>

                <section
                  aria-labelledby="manual-withdrawal-title"
                  style={{ width: '100%' }}
                >
                  <Typography.Title
                    id="manual-withdrawal-title"
                    level={5}
                    style={{ marginTop: 0, marginBottom: 4 }}
                  >
                    手动提现审核
                  </Typography.Title>
                  <Row gutter={16}>
                    <Col xs={24} md={12}>
                      <MoneyField name="manualMinimumYuan" label="手动提现最低额（元）" />
                    </Col>
                    <Col xs={24} md={12}>
                      <MoneyField name="manualMaximumYuan" label="手动提现最高额（元）" />
                    </Col>
                  </Row>
                  <MoneyField
                    name="manualReviewFreeThresholdYuan"
                    label="手动提现自动批准金额上限（元）"
                    help="不超过上限时自动批准，超过时等待人工审核；填 0.00 表示全部人工审核。"
                  />
                </section>

                <section
                  aria-labelledby="auto-withdrawal-title"
                  style={{ width: '100%' }}
                >
                  <Typography.Title
                    id="auto-withdrawal-title"
                    level={5}
                    style={{ marginTop: 0, marginBottom: 4 }}
                  >
                    投递返现自动提现审核
                  </Typography.Title>
                  <Form.Item
                    name="autoWithdrawalEnabled"
                    label={<>审核通过后自动创建提现<HelpTip label="自动创建提现">投递首次审核通过并产生正返现时，按本页规则尝试自动提现。</HelpTip></>}
                    valuePropName="checked"
                  >
                    <Switch checkedChildren="启用" unCheckedChildren="停用" />
                  </Form.Item>
                  {automaticWithdrawalEnabled && (
                    <>
                      <Row gutter={16}>
                        <Col xs={24} md={12}>
                          <MoneyField name="autoMinimumYuan" label="自动提现最低额（元）" />
                        </Col>
                        <Col xs={24} md={12}>
                          <MoneyField name="autoMaximumYuan" label="自动提现最高额（元）" />
                        </Col>
                      </Row>
                      <MoneyField
                        name="autoReviewFreeThresholdYuan"
                        label="自动提现自动批准金额上限（元）"
                        help="不超过上限时自动批准并提交微信，超过时等待人工审核。"
                      />
                    </>
                  )}
                </section>

                {mayManage && (
                  <Space wrap>
                    <Button type="primary" htmlType="submit" loading={submitting}>
                      保存提现配置
                    </Button>
                    <Typography.Text type="secondary">
                      仅影响之后创建的提现
                    </Typography.Text>
                  </Space>
                )}
                <Typography.Text type="secondary">
                  上次保存：{formatShanghaiTime(configuration.publishedAt)}
                </Typography.Text>
              </Space>
            </Form>
          </Card>
        )}
      </Space>
    );
  })();

  return (
    <PageContainer
      {...pageHeader('提现审核规则')}
    >
      <DirectoryScopeBar scope={directory} />
      {content}
    </PageContainer>
  );
}

function MoneyField({
  name,
  label,
  extra,
  help,
}: {
  name: keyof WithdrawalConfigurationForm;
  label: string;
  extra?: React.ReactNode;
  help?: string;
}) {
  return (
    <Form.Item
      name={name}
      label={<>{label}{help && <HelpTip label={label}>{help}</HelpTip>}</>}
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
