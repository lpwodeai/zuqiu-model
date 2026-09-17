import React, { useState, useEffect } from 'react';
import { 
  Card, Table, Select, Tag, Typography, Row, Col, Statistic, 
  Tabs, Input, Space, Button, Modal, Descriptions, Badge, Divider
} from 'antd';
import { 
  UserOutlined, TrophyOutlined, GlobalOutlined, SearchOutlined, 
  BarChartOutlined, StarOutlined, CalendarOutlined, 
  LinkOutlined
} from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { fetchApiSafe } from '../utils/api';

const { Title } = Typography;

const POSITION_GROUPS = [
  { value: '', label: '全部位置' },
  { value: 'goalkeeper', label: '门将' },
  { value: 'defender', label: '后卫' },
  { value: 'midfielder', label: '中场' },
  { value: 'forward', label: '前锋' }
];

const STAT_TYPES = [
  { value: 'goals', label: '进球' },
  { value: 'assists', label: '助攻' },
  { value: 'xg', label: 'xG' },
  { value: 'xA', label: 'xA' },
  { value: 'tackles', label: '抢断' },
  { value: 'interceptions', label: '拦截' },
  { value: 'rating', label: '评分' }
];

const LEAGUE_OPTIONS = [
  { value: '', label: '全部联赛' },
  { value: 'PL', label: '英超' },
  { value: 'SA', label: '西甲' },
  { value: 'BL1', label: '德甲' },
  { value: 'SerieA', label: '意甲' },
  { value: 'FL1', label: '法甲' }
];

function Players() {
  const [players, setPlayers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedPlayer, setSelectedPlayer] = useState(null);
  const [modalVisible, setModalVisible] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [selectedLeague, setSelectedLeague] = useState('');
  const [selectedPosition, setSelectedPosition] = useState('');
  const [selectedStatType, setSelectedStatType] = useState('goals');
  const [leaders, setLeaders] = useState([]);
  const [overview, setOverview] = useState(null);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 });

  useEffect(() => {
    fetchPlayers();
    fetchLeaders();
    fetchOverview();
  }, [pagination.current, selectedLeague, selectedPosition, selectedStatType]);

  const fetchPlayers = async () => {
    setLoading(true);
    const queryParams = new URLSearchParams();
    if (selectedLeague) queryParams.append('league', selectedLeague);
    if (selectedPosition) queryParams.append('positionGroup', selectedPosition);
    if (searchText) queryParams.append('search', searchText);
    queryParams.append('page', pagination.current);
    queryParams.append('limit', pagination.pageSize);

    try {
      const data = await fetchApiSafe(`/api/players?${queryParams.toString()}`);
      if (data.success) {
        setPlayers(data.data);
        setPagination(prev => ({ ...prev, total: data.total }));
      }
    } finally {
      setLoading(false);
    }
  };

  const fetchLeaders = async () => {
    try {
      const data = await fetchApiSafe(`/api/players/stats/leaders?statType=${selectedStatType}&league=${selectedLeague}&limit=10`);
      if (data.success) {
        setLeaders(data.data);
      }
    } catch (err) {
      console.error('获取榜单失败:', err);
    }
  };

  const fetchOverview = async () => {
    try {
      const data = await fetchApiSafe(`/api/players/stats/overview?league=${selectedLeague}`);
      if (data.success) {
        setOverview(data.data);
      }
    } catch (err) {
      console.error('获取概览失败:', err);
    }
  };

  const handlePlayerClick = (player) => {
    setSelectedPlayer(player);
    setModalVisible(true);
  };

  const getPositionLabel = (group) => {
    const map = { goalkeeper: '门将', defender: '后卫', midfielder: '中场', forward: '前锋' };
    return map[group] || group;
  };

  const getPositionColor = (group) => {
    const map = { goalkeeper: 'purple', defender: 'blue', midfielder: 'green', forward: 'red' };
    return map[group] || 'default';
  };

  const getStatusBadge = (status) => {
    const map = {
      available: { color: 'success', text: '可用' },
      injured: { color: 'error', text: '受伤' },
      suspended: { color: 'warning', text: '停赛' }
    };
    const config = map[status] || { color: 'default', text: status };
    return <Badge status={config.color} text={config.text} />;
  };

  const playerColumns = [
    { 
      title: '球员', 
      dataIndex: 'name', 
      key: 'name', 
      sorter: (a, b) => a.name.localeCompare(b.name),
      render: (text, record) => (
        <Space>
          {record.isKeyPlayer && <StarOutlined style={{ color: '#fbbf24' }} />}
          <span style={{ color: '#f1f5f9', fontWeight: 'bold', fontSize: '14px' }}>{text}</span>
          {record.nameEn && <span style={{ color: '#64748b', fontSize: '12px' }}>({record.nameEn})</span>}
        </Space>
      )
    },
    { 
      title: '位置', 
      dataIndex: 'positionGroup', 
      key: 'positionGroup',
      render: (v) => <Tag color={getPositionColor(v)}>{getPositionLabel(v)}</Tag>
    },
    { 
      title: '球队', 
      dataIndex: 'teamName', 
      key: 'teamName',
      render: (text, record) => (
        <span style={{ color: '#94a3b8' }}>
          {text} <Tag color="default">{record.teamLeague}</Tag>
        </span>
      )
    },
    { 
      title: '年龄', 
      dataIndex: 'age', 
      key: 'age',
      sorter: (a, b) => a.age - b.age,
      render: (v) => <span style={{ color: '#94a3b8' }}>{v}</span>
    },
    { 
      title: '身价', 
      dataIndex: 'marketValue', 
      key: 'marketValue',
      sorter: (a, b) => a.marketValue - b.marketValue,
      render: (v) => <span style={{ color: '#10b981', fontWeight: '500' }}>{v}亿€</span>
    },
    { 
      title: '状态', 
      dataIndex: 'playerStatus', 
      key: 'playerStatus',
      render: (v) => getStatusBadge(v)
    },
    {
      title: '操作',
      key: 'action',
      render: (_, record) => (
        <Button type="link" onClick={() => handlePlayerClick(record)}>
          详情
        </Button>
      )
    }
  ];

  const leaderColumns = [
    { 
      title: '排名', 
      key: 'rank', 
      render: (_, __, index) => (
        <span style={{ fontWeight: 'bold', fontSize: '14px', color: index < 3 ? '#fbbf24' : '#94a3b8' }}>
          {index + 1}
        </span>
      )
    },
    { 
      title: '球员', 
      key: 'name',
      render: (_, record) => (
        <Space>
          <span style={{ color: '#f1f5f9', fontWeight: 'bold' }}>{record.name}</span>
          <span style={{ color: '#64748b', fontSize: '12px' }}>({record.nameEn})</span>
        </Space>
      )
    },
    { 
      title: '位置', 
      key: 'positionGroup',
      render: (_, record) => <Tag color={getPositionColor(record.positionGroup)}>{getPositionLabel(record.positionGroup)}</Tag>
    },
    { 
      title: '球队', 
      key: 'teamName',
      render: (_, record) => <span style={{ color: '#94a3b8' }}>{record.teamName}</span>
    },
    { 
      title: '场次', 
      key: 'matches',
      render: (_, record) => <span style={{ color: '#94a3b8' }}>{record.matches}</span>
    },
    { 
      title: '分钟', 
      key: 'minutes',
      render: (_, record) => <span style={{ color: '#94a3b8' }}>{record.minutes}</span>
    },
    { 
      title: '进球', 
      key: 'goals',
      render: (_, record) => <span style={{ color: '#ef4444', fontWeight: 'bold' }}>{record.goals}</span>
    },
    { 
      title: '助攻', 
      key: 'assists',
      render: (_, record) => <span style={{ color: '#3b82f6', fontWeight: 'bold' }}>{record.assists}</span>
    },
    { 
      title: '评分', 
      key: 'rating',
      render: (_, record) => <span style={{ color: '#fbbf24', fontWeight: 'bold' }}>{record.rating?.toFixed(2)}</span>
    },
    { 
      title: `${STAT_TYPES.find(s => s.value === selectedStatType)?.label}`, 
      key: 'value',
      render: (_, record) => <span style={{ color: '#10b981', fontWeight: 'bold', fontSize: '16px' }}>{record.value}</span>
    }
  ];

  const playerStatsChart = selectedPlayer?.stats?.[0] ? {
    title: { text: `${selectedPlayer.name} 赛季数据`, left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
    legend: { data: ['进球', '助攻', 'xG', 'xA'], bottom: '5%', textStyle: { color: '#94a3b8', fontSize: 12 } },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '10%', containLabel: true },
    xAxis: { type: 'category', data: ['进攻数据'], axisLabel: { color: '#94a3b8' }, axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } } },
    yAxis: { type: 'value', axisLabel: { color: '#94a3b8' }, axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } } },
    series: [
      { name: '进球', type: 'bar', data: [selectedPlayer.stats[0].goals], itemStyle: { color: '#ef4444' } },
      { name: '助攻', type: 'bar', data: [selectedPlayer.stats[0].assists], itemStyle: { color: '#3b82f6' } },
      { name: 'xG', type: 'bar', data: [selectedPlayer.stats[0].xg], itemStyle: { color: '#f59e0b' } },
      { name: 'xA', type: 'bar', data: [selectedPlayer.stats[0].xA], itemStyle: { color: '#8b5cf6' } }
    ]
  } : {};

  return (
    <div className="fade-in">
      <div style={{ marginBottom: 24 }}>
        <Title level={2} style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <UserOutlined style={{ color: '#8b5cf6' }} />
          <span style={{ background: 'linear-gradient(90deg, #8b5cf6, #ec4899)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>球员统计</span>
        </Title>
      </div>

      {overview && (
        <Row gutter={16} style={{ marginBottom: 24 }}>
          <Col span={6}>
            <Card>
              <Statistic title="球员总数" value={overview.totalPlayers} prefix={<UserOutlined />} valueStyle={{ color: '#f1f5f9' }} />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic title="平均年龄" value={overview.avgAge} suffix="岁" prefix={<CalendarOutlined />} valueStyle={{ color: '#94a3b8' }} />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic title="总进球" value={overview.totalGoals} prefix={<TrophyOutlined />} valueStyle={{ color: '#ef4444' }} />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic title="总助攻" value={overview.totalAssists} prefix={<LinkOutlined />} valueStyle={{ color: '#3b82f6' }} />
            </Card>
          </Col>
        </Row>
      )}

      <Tabs defaultActiveKey="players" style={{ marginBottom: 24 }}>
        <Tabs.TabPane tab={<span><BarChartOutlined /> 球员列表</span>} key="players">
          <Card style={{ marginBottom: 24 }}>
            <Space>
              <Input 
                placeholder="搜索球员" 
                style={{ width: 300 }} 
                allowClear
                prefix={<SearchOutlined />}
                value={searchText}
                onChange={(e) => { setSearchText(e.target.value); setPagination({ current: 1, pageSize: 20, total: 0 }); }}
              />
              <Select 
                style={{ width: 150 }} 
                placeholder="选择联赛" 
                allowClear
                value={selectedLeague}
                onChange={(v) => { setSelectedLeague(v); setPagination({ current: 1, pageSize: 20, total: 0 }); }}
              >
                {LEAGUE_OPTIONS.map(opt => (
                  <Select.Option key={opt.value} value={opt.value}>{opt.label}</Select.Option>
                ))}
              </Select>
              <Select 
                style={{ width: 150 }} 
                placeholder="选择位置" 
                allowClear
                value={selectedPosition}
                onChange={(v) => { setSelectedPosition(v); setPagination({ current: 1, pageSize: 20, total: 0 }); }}
              >
                {POSITION_GROUPS.map(opt => (
                  <Select.Option key={opt.value} value={opt.value}>{opt.label}</Select.Option>
                ))}
              </Select>
            </Space>
          </Card>

          <Table
            columns={playerColumns}
            dataSource={players}
            rowKey="id"
            loading={loading}
            pagination={{
              ...pagination,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 名球员`,
              onChange: (page) => setPagination(prev => ({ ...prev, current: page }))
            }}
            style={{ color: '#f1f5f9' }}
          />
        </Tabs.TabPane>

        <Tabs.TabPane tab={<span><TrophyOutlined /> 数据榜单</span>} key="leaders">
          <Card style={{ marginBottom: 24 }}>
            <Space>
              <Select 
                style={{ width: 150 }} 
                placeholder="选择联赛" 
                allowClear
                value={selectedLeague}
                onChange={setSelectedLeague}
              >
                {LEAGUE_OPTIONS.map(opt => (
                  <Select.Option key={opt.value} value={opt.value}>{opt.label}</Select.Option>
                ))}
              </Select>
              <Select 
                style={{ width: 150 }} 
                placeholder="统计类型" 
                value={selectedStatType}
                onChange={setSelectedStatType}
              >
                {STAT_TYPES.map(opt => (
                  <Select.Option key={opt.value} value={opt.value}>{opt.label}</Select.Option>
                ))}
              </Select>
            </Space>
          </Card>

          <Table
            columns={leaderColumns}
            dataSource={leaders}
            rowKey={(record, index) => index}
            pagination={false}
            style={{ color: '#f1f5f9' }}
          />
        </Tabs.TabPane>
      </Tabs>

      <Modal
        title={`${selectedPlayer?.name} 详细信息`}
        visible={modalVisible}
        onCancel={() => setModalVisible(false)}
        footer={null}
        width={800}
      >
        {selectedPlayer && (
          <div>
            <Descriptions title="基本信息" column={2} bordered>
              <Descriptions.Item label="英文名">{selectedPlayer.nameEn}</Descriptions.Item>
              <Descriptions.Item label="位置">{getPositionLabel(selectedPlayer.positionGroup)}</Descriptions.Item>
              <Descriptions.Item label="球队">{selectedPlayer.teamName}</Descriptions.Item>
              <Descriptions.Item label="联赛">{selectedPlayer.teamLeague}</Descriptions.Item>
              <Descriptions.Item label="年龄">{selectedPlayer.age} 岁</Descriptions.Item>
              <Descriptions.Item label="身高">{selectedPlayer.height} cm</Descriptions.Item>
              <Descriptions.Item label="体重">{selectedPlayer.weight} kg</Descriptions.Item>
              <Descriptions.Item label="国籍">{selectedPlayer.nationality}</Descriptions.Item>
              <Descriptions.Item label="球衣号码">{selectedPlayer.jerseyNumber}</Descriptions.Item>
              <Descriptions.Item label="惯用脚">{selectedPlayer.foot}</Descriptions.Item>
              <Descriptions.Item label="身价">{selectedPlayer.marketValue} 亿€</Descriptions.Item>
              <Descriptions.Item label="核心球员">{selectedPlayer.isKeyPlayer ? '是' : '否'}</Descriptions.Item>
              <Descriptions.Item label="阵容角色">{selectedPlayer.lineupRole === 'starter' ? '主力' : selectedPlayer.lineupRole === 'backup' ? '替补' : '青训'}</Descriptions.Item>
              <Descriptions.Item label="状态">{getStatusBadge(selectedPlayer.playerStatus)}</Descriptions.Item>
            </Descriptions>

            <Divider />

            <Title level={4}>赛季统计</Title>
            {selectedPlayer.stats && selectedPlayer.stats.length > 0 ? (
              selectedPlayer.stats.map((stat, idx) => (
                <div key={idx} style={{ marginBottom: 24 }}>
                  <h4 style={{ marginBottom: 16 }}>{stat.season}赛季</h4>
                  <Row gutter={16}>
                    <Col span={4}><Statistic title="场次" value={stat.matches} valueStyle={{ color: '#94a3b8' }} /></Col>
                    <Col span={4}><Statistic title="首发" value={stat.starts} valueStyle={{ color: '#94a3b8' }} /></Col>
                    <Col span={4}><Statistic title="分钟" value={stat.minutes} valueStyle={{ color: '#94a3b8' }} /></Col>
                    <Col span={4}><Statistic title="进球" value={stat.goals} valueStyle={{ color: '#ef4444' }} /></Col>
                    <Col span={4}><Statistic title="助攻" value={stat.assists} valueStyle={{ color: '#3b82f6' }} /></Col>
                    <Col span={4}><Statistic title="评分" value={stat.rating?.toFixed(2)} valueStyle={{ color: '#fbbf24' }} /></Col>
                  </Row>
                  <Row gutter={16} style={{ marginTop: 16 }}>
                    <Col span={4}><Statistic title="xG" value={stat.xg?.toFixed(2)} valueStyle={{ color: '#f59e0b' }} /></Col>
                    <Col span={4}><Statistic title="xA" value={stat.xA?.toFixed(2)} valueStyle={{ color: '#8b5cf6' }} /></Col>
                    <Col span={4}><Statistic title="射门" value={stat.shots} valueStyle={{ color: '#94a3b8' }} /></Col>
                    <Col span={4}><Statistic title="射正" value={stat.shotsOnTarget} valueStyle={{ color: '#94a3b8' }} /></Col>
                    <Col span={4}><Statistic title="抢断" value={stat.tackles} valueStyle={{ color: '#10b981' }} /></Col>
                    <Col span={4}><Statistic title="拦截" value={stat.interceptions} valueStyle={{ color: '#10b981' }} /></Col>
                  </Row>
                  <Row gutter={16} style={{ marginTop: 16 }}>
                    <Col span={4}><Statistic title="传球数" value={stat.passes} valueStyle={{ color: '#94a3b8' }} /></Col>
                    <Col span={4}><Statistic title="传球成功率" value={stat.passAccuracy} suffix="%" valueStyle={{ color: '#94a3b8' }} /></Col>
                    <Col span={4}><Statistic title="关键传球" value={stat.keyPasses} valueStyle={{ color: '#94a3b8' }} /></Col>
                    <Col span={4}><Statistic title="盘带成功" value={stat.dribblesCompleted} valueStyle={{ color: '#94a3b8' }} /></Col>
                    <Col span={4}><Statistic title="盘带成功率" value={stat.dribbleSuccess} suffix="%" valueStyle={{ color: '#94a3b8' }} /></Col>
                    <Col span={4}><Statistic title="空中对抗" value={stat.aerialWon} valueStyle={{ color: '#94a3b8' }} /></Col>
                  </Row>
                </div>
              ))
            ) : (
              <p style={{ textAlign: 'center', color: '#64748b' }}>暂无统计数据</p>
            )}

            {playerStatsChart && selectedPlayer.stats?.[0] && (
              <div>
                <Divider />
                <Title level={4}>数据可视化</Title>
                <ReactECharts option={playerStatsChart} style={{ height: 300 }} />
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}

export default Players;