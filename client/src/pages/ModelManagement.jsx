import React, { useState, useEffect, useCallback } from 'react';
import {
  Card,
  Row,
  Col,
  Button,
  Space,
  Table,
  Tag,
  Switch,
  Spin,
  message,
  Progress,
  Statistic,
  Divider,
  Alert,
  Tooltip,
  Badge
} from 'antd';
import {
  ReloadOutlined,
  ApiOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
  FileTextOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
  SyncOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  InfoCircleOutlined,
  TeamOutlined
} from '@ant-design/icons';

const MODEL_KEYS = [
  { key: 'xgb', name: 'XGBoost 模型', color: '#f97316' },
  { key: 'lgb', name: 'LightGBM 模型', color: '#22c55e' },
  { key: 'elo', name: 'Elo 评分模型', color: '#3b82f6' },
  { key: 'poisson', name: 'Poisson 模型', color: '#8b5cf6' },
  { key: 'dixonCole', name: 'Dixon-Coles 模型', color: '#ec4899' },
  { key: 'ssm', name: 'SSM 模型', color: '#06b6d4' }
];

function ModelManagement() {
  const [loading, setLoading] = useState(true);
  const [modelInfo, setModelInfo] = useState(null);
  const [reloadStatus, setReloadStatus] = useState(null);
  const [history, setHistory] = useState([]);
  const [reloadingKey, setReloadingKey] = useState(null);
  const [reloadingAll, setReloadingAll] = useState(false);
  const [watchStatus, setWatchStatus] = useState({ watching: false });
  const [watchLoading, setWatchLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem('token');
      const headers = token ? { Authorization: `Bearer ${token}` } : {};

      const [infoRes, statusRes, historyRes] = await Promise.allSettled([
        fetch('/api/model/info', { headers }).then(r => r.json()),
        fetch('/api/model/reload/status', { headers }).then(r => r.json()),
        fetch('/api/model/reload/history?limit=20', { headers }).then(r => r.json())
      ]);

      if (infoRes.status === 'fulfilled' && infoRes.value.success) {
        setModelInfo(infoRes.value.data);
      } else if (infoRes.status === 'rejected') {
        console.warn('获取模型信息失败:', infoRes.reason);
      }

      if (statusRes.status === 'fulfilled' && statusRes.value.success) {
        setReloadStatus(statusRes.value.data);
        setWatchStatus({ watching: statusRes.value.data.watching || false });
      }

      if (historyRes.status === 'fulfilled' && historyRes.value.success) {
        setHistory(historyRes.value.data || []);
      }
    } catch (error) {
      console.error('获取模型管理数据失败:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleReloadSingle = async (key) => {
    setReloadingKey(key);
    try {
      const token = localStorage.getItem('token');
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const res = await fetch(`/api/model/reload/${key}`, {
        method: 'POST',
        headers
      });
      const data = await res.json();
      if (data.success) {
        message.success(`${key} 模型热更新成功`);
        fetchData();
      } else {
        message.error(data.message || '热更新失败');
      }
    } catch (error) {
      message.error(`热更新失败: ${error.message}`);
    } finally {
      setReloadingKey(null);
    }
  };

  const handleReloadAll = async () => {
    setReloadingAll(true);
    try {
      const token = localStorage.getItem('token');
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const res = await fetch('/api/model/reload-all', {
        method: 'POST',
        headers
      });
      const data = await res.json();
      if (data.success) {
        message.success(data.message);
        fetchData();
      } else {
        message.error(data.message || '全量重载失败');
      }
    } catch (error) {
      message.error(`全量重载失败: ${error.message}`);
    } finally {
      setReloadingAll(false);
    }
  };

  const handleWatchToggle = async (checked) => {
    setWatchLoading(true);
    try {
      const token = localStorage.getItem('token');
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const endpoint = checked ? '/api/model/watch/start' : '/api/model/watch/stop';
      const res = await fetch(endpoint, {
        method: 'POST',
        headers
      });
      const data = await res.json();
      if (data.success) {
        message.success(data.message);
        setWatchStatus({ watching: checked });
      } else {
        message.error(data.message || '操作失败');
      }
    } catch (error) {
      message.error(`操作失败: ${error.message}`);
    } finally {
      setWatchLoading(false);
    }
  };

  const getModelStatus = (key) => {
    if (!reloadStatus?.modelStatus) return 'unknown';
    return reloadStatus.modelStatus[key] || 'unknown';
  };

  const getStatusTag = (status) => {
    const map = {
      active: { color: 'green', text: '已加载' },
      inactive: { color: 'default', text: '未加载' },
      loading: { color: 'blue', text: '加载中' },
      error: { color: 'red', text: '错误' },
      unknown: { color: 'orange', text: '未知' }
    };
    const config = map[status] || map.unknown;
    return <Tag color={config.color}>{config.text}</Tag>;
  };

  const historyColumns = [
    {
      title: '时间',
      dataIndex: 'timestamp',
      key: 'timestamp',
      render: (text) => new Date(text).toLocaleString('zh-CN')
    },
    {
      title: '模型',
      dataIndex: 'key',
      key: 'key',
      render: (text) => {
        const model = MODEL_KEYS.find(m => m.key === text);
        return model ? model.name : text;
      }
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (text) => {
        if (text === 'success') return <Tag color="green"><CheckCircleOutlined /> 成功</Tag>;
        if (text === 'failed') return <Tag color="red"><CloseCircleOutlined /> 失败</Tag>;
        return <Tag>{text}</Tag>;
      }
    },
    {
      title: '耗时(ms)',
      dataIndex: 'duration',
      key: 'duration',
      render: (text) => <span style={{ color: '#94a3b8' }}>{text || '-'}</span>
    },
    {
      title: '大小(KB)',
      dataIndex: 'size',
      key: 'size',
      render: (text) => <span style={{ color: '#94a3b8' }}>{text || '-'}</span>
    }
  ];

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 100 }}>
        <Spin size="large">
          <div style={{ textAlign: 'center', padding: 20, color: '#94a3b8' }}>加载模型管理数据...</div>
        </Spin>
      </div>
    );
  }

  const teamsCount = modelInfo?.predictionService?.teamsCount || 0;
  const eloCount = modelInfo?.predictionService?.eloRatingsCount || 0;
  const modelsLoaded = Object.values(reloadStatus?.modelStatus || {}).filter(
    s => s === 'active'
  ).length;
  const totalModels = MODEL_KEYS.length;

  return (
    <div style={{ padding: '8px' }}>
      <div style={{ marginBottom: 24, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ margin: 0, color: '#f1f5f9' }}>模型热更新管理</h2>
          <p style={{ margin: '4px 0 0', color: '#94a3b8' }}>
            实时监控模型状态，手动重载模型，管理文件监控
          </p>
        </div>
        <Space>
          <Button
            type="primary"
            icon={<SyncOutlined spin={reloadingAll} />}
            onClick={handleReloadAll}
            loading={reloadingAll}
            size="large"
          >
            全量重载
          </Button>
          <Button icon={<ReloadOutlined />} onClick={fetchData} size="large">
            刷新
          </Button>
        </Space>
      </div>

      {/* 系统概览卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} md={6}>
          <Card style={{ background: 'rgba(30, 41, 59, 0.6)', border: '1px solid rgba(255,255,255,0.05)' }}>
            <Statistic
              title={<span style={{ color: '#94a3b8' }}>已加载模型</span>}
              value={modelsLoaded}
              suffix={`/ ${totalModels}`}
              valueStyle={{ color: modelsLoaded >= 4 ? '#22c55e' : '#f97316' }}
              prefix={<ApiOutlined />}
            />
            <Progress
              percent={Math.round((modelsLoaded / totalModels) * 100)}
              size="small"
              style={{ marginTop: 8 }}
              strokeColor={modelsLoaded >= 4 ? '#22c55e' : '#f97316'}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card style={{ background: 'rgba(30, 41, 59, 0.6)', border: '1px solid rgba(255,255,255,0.05)' }}>
            <Statistic
              title={<span style={{ color: '#94a3b8' }}>球队数据</span>}
              value={teamsCount}
              suffix="支"
              valueStyle={{ color: '#3b82f6' }}
              prefix={<TeamOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card style={{ background: 'rgba(30, 41, 59, 0.6)', border: '1px solid rgba(255,255,255,0.05)' }}>
            <Statistic
              title={<span style={{ color: '#94a3b8' }}>Elo 评分</span>}
              value={eloCount}
              suffix="条"
              valueStyle={{ color: '#8b5cf6' }}
              prefix={<DatabaseOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card style={{ background: 'rgba(30, 41, 59, 0.6)', border: '1px solid rgba(255,255,255,0.05)' }}>
            <Statistic
              title={<span style={{ color: '#94a3b8' }}>文件监控</span>}
              value={watchStatus.watching ? '运行中' : '已停止'}
              valueStyle={{ color: watchStatus.watching ? '#22c55e' : '#64748b', fontSize: 24 }}
              prefix={<EyeOutlined />}
            />
          </Card>
        </Col>
      </Row>

      {/* 模型状态卡片 */}
      <Card
        title={<span style={{ color: '#f1f5f9' }}><ApiOutlined /> 模型状态</span>}
        style={{ marginBottom: 24, background: 'rgba(30, 41, 59, 0.6)', border: '1px solid rgba(255,255,255,0.05)' }}
        styles={{ header: { borderBottom: '1px solid rgba(255,255,255,0.1)' } }}
      >
        <Row gutter={[16, 16]}>
          {MODEL_KEYS.map(model => {
            const status = getModelStatus(model.key);
            const isReloading = reloadingKey === model.key;
            const isActive = status === 'active';

            return (
              <Col xs={24} sm={12} md={8} lg={8} xl={8} key={model.key}>
                <Card
                  size="small"
                  style={{
                    background: 'rgba(51, 65, 85, 0.6)',
                    border: `1px solid ${isActive ? model.color + '40' : 'rgba(255,255,255,0.05)'}`,
                    boxShadow: isActive ? `0 0 12px ${model.color}20` : 'none'
                  }}
                  styles={{ body: { padding: 16 } }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
                    <div>
                      <div style={{ fontSize: 14, fontWeight: 600, color: '#f1f5f9', marginBottom: 4 }}>
                        {model.name}
                      </div>
                      {getStatusTag(status)}
                    </div>
                    <div
                      style={{
                        width: 36,
                        height: 36,
                        borderRadius: 8,
                        background: model.color + '20',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center'
                      }}
                    >
                      <ThunderboltOutlined style={{ color: model.color, fontSize: 18 }} />
                    </div>
                  </div>
                  <Button
                    block
                    size="small"
                    icon={<ReloadOutlined spin={isReloading} />}
                    loading={isReloading}
                    onClick={() => handleReloadSingle(model.key)}
                    style={{
                      background: isActive ? model.color + '20' : 'rgba(59, 130, 246, 0.2)',
                      borderColor: isActive ? model.color : '#3b82f6',
                      color: '#f1f5f9'
                    }}
                  >
                    热更新
                  </Button>
                </Card>
              </Col>
            );
          })}
        </Row>
      </Card>

      {/* 文件监控与全局操作 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} md={12}>
          <Card
            title={<span style={{ color: '#f1f5f9' }}><EyeOutlined /> 文件监控</span>}
            style={{ background: 'rgba(30, 41, 59, 0.6)', border: '1px solid rgba(255,255,255,0.05)' }}
            styles={{ header: { borderBottom: '1px solid rgba(255,255,255,0.1)' } }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ color: '#f1f5f9', marginBottom: 4 }}>
                  自动监控模型文件变更
                </div>
                <div style={{ color: '#94a3b8', fontSize: 12 }}>
                  当模型文件发生变化时自动触发热更新
                </div>
              </div>
              <Switch
                checked={watchStatus.watching}
                onChange={handleWatchToggle}
                loading={watchLoading}
                checkedChildren="开启"
                unCheckedChildren="关闭"
              />
            </div>
            <Divider style={{ borderColor: 'rgba(255,255,255,0.1)', margin: '16px 0' }} />
            <Alert
              type={watchStatus.watching ? 'success' : 'info'}
              showIcon
              icon={watchStatus.watching ? <EyeOutlined /> : <EyeInvisibleOutlined />}
              message={watchStatus.watching ? '监控运行中' : '监控已停止'}
              description={
                watchStatus.watching
                  ? '正在实时监控模型文件变更，检测到变化时将自动重载'
                  : '启动监控后，模型文件变更将自动触发热更新'
              }
              style={{ background: watchStatus.watching ? 'rgba(34,197,94,0.1)' : 'rgba(59,130,246,0.1)', border: 'none' }}
            />
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card
            title={<span style={{ color: '#f1f5f9' }}><ReloadOutlined /> 全局操作</span>}
            style={{ background: 'rgba(30, 41, 59, 0.6)', border: '1px solid rgba(255,255,255,0.05)' }}
            styles={{ header: { borderBottom: '1px solid rgba(255,255,255,0.1)' } }}
          >
            <Space direction="vertical" style={{ width: '100%' }} size="middle">
              <Button
                type="primary"
                block
                size="large"
                icon={<SyncOutlined spin={reloadingAll} />}
                onClick={handleReloadAll}
                loading={reloadingAll}
              >
                全量重载所有模型
              </Button>
              <Button
                block
                size="large"
                icon={<ReloadOutlined />}
                onClick={fetchData}
              >
                刷新状态
              </Button>
            </Space>
          </Card>
        </Col>
      </Row>

      {/* 重载历史 */}
      <Card
        title={<span style={{ color: '#f1f5f9' }}><FileTextOutlined /> 重载历史</span>}
        style={{ background: 'rgba(30, 41, 59, 0.6)', border: '1px solid rgba(255,255,255,0.05)' }}
        styles={{ header: { borderBottom: '1px solid rgba(255,255,255,0.1)' } }}
      >
        <Table
          columns={historyColumns}
          dataSource={history}
          rowKey="id"
          pagination={{ pageSize: 10, showSizeChanger: false }}
          style={{ background: 'transparent' }}
          locale={{ emptyText: '暂无重载历史' }}
        />
      </Card>
    </div>
  );
}

export default ModelManagement;
