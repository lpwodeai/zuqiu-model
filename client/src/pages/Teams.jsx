import React, { useState, useEffect } from 'react';
import { 
  Card, Table, Select, Tag, Typography, Row, Col, Statistic, 
  Tabs, Input, Space, Button, Modal, Descriptions 
} from 'antd';
import { TeamOutlined, TrophyOutlined, GlobalOutlined, SearchOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { fetchApiSafe } from '../utils/api';

const { Title } = Typography;

function Teams() {
  const [teams, setTeams] = useState([]);
  const [groups, setGroups] = useState({});
  const [loading, setLoading] = useState(false);
  const [selectedTeam, setSelectedTeam] = useState(null);
  const [modalVisible, setModalVisible] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [selectedGroup, setSelectedGroup] = useState('');

  useEffect(() => {
    setLoading(true);
    fetchApiSafe('/api/teams')
      .then(data => {
        if (data.success) {
          setTeams(data.data);
        }
      })
      .finally(() => setLoading(false));

    fetchApiSafe('/api/teams/groups')
      .then(data => {
        if (data.success) {
          setGroups(data.data);
        }
      });
  }, []);

  const filteredTeams = teams.filter(team => {
    const matchesSearch = team.name.toLowerCase().includes(searchText.toLowerCase());
    const matchesGroup = !selectedGroup || team.league === selectedGroup;
    return matchesSearch && matchesGroup;
  });

  const teamCompareChart = selectedTeam ? {
    title: { text: `${selectedTeam.name} 数据分析`, left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
    legend: { data: ['进攻', '防守', '身价', '节奏', '逼抢', '凝聚力'], bottom: '5%', textStyle: { color: '#94a3b8', fontSize: 12 } },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '10%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: ['进攻', '防守', '身价', '节奏', '逼抢', '凝聚力'],
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: { 
      type: 'value',
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      name: selectedTeam.name,
      type: 'bar',
      data: [
        selectedTeam.attack * 10,
        selectedTeam.defence * 10,
        selectedTeam.marketValue * 10,
        selectedTeam.tempo * 10,
        selectedTeam.pressIntensity * 10,
        selectedTeam.cohesion * 10
      ],
      itemStyle: { color: 'linear-gradient(135deg, #3b82f6, #2563eb)' }
    }]
  } : {};

  const groupDistributionChart = {
    title: { text: '联赛球队分布', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '10%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: Object.keys(groups).sort(),
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: { 
      type: 'value', 
      name: '球队数量',
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      data: Object.entries(groups).map(([k, v]) => v.length).sort(),
      type: 'bar',
      itemStyle: { color: 'linear-gradient(135deg, #10b981, #059669)' }
    }]
  };

  const columns = [
    { title: '球队', dataIndex: 'name', key: 'name', sorter: (a, b) => a.name.localeCompare(b.name), render: (text) => <span style={{ color: '#f1f5f9', fontWeight: 'bold', fontSize: '14px' }}>{text}</span> },
    { title: '联赛', dataIndex: 'league', key: 'league', render: (g) => <Tag color="blue">{g}</Tag> },
    { title: '进攻', dataIndex: 'attack', key: 'attack', sorter: (a, b) => a.attack - b.attack, render: (v) => <span style={{ color: '#94a3b8', fontWeight: '500' }}>{v.toFixed(2)}</span> },
    { title: '防守', dataIndex: 'defence', key: 'defence', sorter: (a, b) => a.defence - b.defence, render: (v) => <span style={{ color: '#94a3b8', fontWeight: '500' }}>{v.toFixed(2)}</span> },
    { title: 'xG', dataIndex: 'xGOT', key: 'xGOT', render: (v) => <span style={{ color: '#94a3b8', fontWeight: '500' }}>{v ? v.toFixed(2) : '-'}</span> },
    { title: 'xGA', dataIndex: 'xGA', key: 'xGA', render: (v) => <span style={{ color: '#94a3b8', fontWeight: '500' }}>{v ? v.toFixed(2) : '-'}</span> },
    { title: '战术', dataIndex: 'tactical', key: 'tactical', render: (v) => <Tag color="green">{v}</Tag> },
    { title: '身价', dataIndex: 'marketValue', key: 'marketValue', render: (v) => <span style={{ color: '#94a3b8', fontWeight: '500' }}>{v}亿€</span> },
    {
      title: '操作',
      key: 'action',
      render: (_, record) => (
        <Button type="link" onClick={() => { setSelectedTeam(record); setModalVisible(true); }}>
          详情
        </Button>
      )
    }
  ];

  return (
    <div className="fade-in">
      <div style={{ marginBottom: 24 }}>
        <Title level={2} style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <TeamOutlined style={{ color: '#3b82f6' }} />
          <span style={{ background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>球队数据</span>
        </Title>
      </div>

      <Card style={{ marginBottom: 24 }}>
        <Space>
          <Input 
            placeholder="搜索球队" 
            style={{ width: 300 }} 
            allowClear
            prefix={<SearchOutlined />}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
          <Select 
            style={{ width: 150 }} 
            placeholder="选择联赛" 
            allowClear
            value={selectedGroup}
            onChange={setSelectedGroup}
          >
            {Object.keys(groups).map(g => (
              <Select.Option key={g} value={g}>{g}</Select.Option>
            ))}
          </Select>
        </Space>
      </Card>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card hoverable>
            <Statistic title="参赛球队" value={teams.length} prefix={<TeamOutlined />} valueStyle={{ fontSize: 28 }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card hoverable>
            <Statistic title="联赛数量" value={Object.keys(groups).length} prefix={<GlobalOutlined />} valueStyle={{ fontSize: 28 }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card hoverable>
            <Statistic title="最强进攻" value={teams.length > 0 ? Math.max(...teams.map(t => t.attack)).toFixed(2) : 0} prefix={<TrophyOutlined />} valueStyle={{ fontSize: 28, color: '#f59e0b' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card hoverable>
            <Statistic title="总身价" value={teams.reduce((sum, t) => sum + (t.marketValue || 0), 0).toFixed(1)} suffix="亿€" valueStyle={{ fontSize: 28, color: '#10b981' }} />
          </Card>
        </Col>
      </Row>

      <Card title="联赛分布" style={{ marginBottom: 24 }}>
        <ReactECharts option={groupDistributionChart} style={{ height: 400 }} opts={{ renderer: 'svg' }} />
      </Card>

      <Card title="球队列表">
        <Table 
          dataSource={filteredTeams} 
          columns={columns}
          loading={loading}
          pagination={{ pageSize: 12 }}
          rowKey="key"
        />
      </Card>

      <Modal
        title={<span style={{ color: '#f1f5f9' }}>{selectedTeam?.name}</span>}
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        footer={null}
        width={800}
      >
        {selectedTeam && (
          <div>
            <Descriptions bordered column={2}>
              <Descriptions.Item label="联赛"><span style={{ color: '#f1f5f9' }}>{selectedTeam.league}</span></Descriptions.Item>
              <Descriptions.Item label="战术风格"><span style={{ color: '#f1f5f9' }}>{selectedTeam.tactical}</span></Descriptions.Item>
              <Descriptions.Item label="进攻系数"><span style={{ color: '#94a3b8' }}>{selectedTeam.attack?.toFixed(2)}</span></Descriptions.Item>
              <Descriptions.Item label="防守系数"><span style={{ color: '#94a3b8' }}>{selectedTeam.defence?.toFixed(2)}</span></Descriptions.Item>
              <Descriptions.Item label="预期进球"><span style={{ color: '#94a3b8' }}>{selectedTeam.xGOT?.toFixed(2)}</span></Descriptions.Item>
              <Descriptions.Item label="预期失球"><span style={{ color: '#94a3b8' }}>{selectedTeam.xGA?.toFixed(2)}</span></Descriptions.Item>
              <Descriptions.Item label="身价"><span style={{ color: '#94a3b8' }}>{selectedTeam.marketValue}亿€</span></Descriptions.Item>
              <Descriptions.Item label="球队凝聚力"><span style={{ color: '#94a3b8' }}>{selectedTeam.cohesion?.toFixed(2)}</span></Descriptions.Item>
              <Descriptions.Item label="近期状态"><span style={{ color: selectedTeam.recentForm > 0 ? '#10b981' : selectedTeam.recentForm < 0 ? '#ef4444' : '#94a3b8' }}>{selectedTeam.recentForm > 0 ? '↑' : selectedTeam.recentForm < 0 ? '↓' : '→'} {selectedTeam.recentForm?.toFixed(2)}</span></Descriptions.Item>
              <Descriptions.Item label="节奏"><span style={{ color: '#94a3b8' }}>{selectedTeam.tempo?.toFixed(2)}</span></Descriptions.Item>
              <Descriptions.Item label="逼抢强度"><span style={{ color: '#94a3b8' }}>{selectedTeam.pressIntensity?.toFixed(2)}</span></Descriptions.Item>
              <Descriptions.Item label="威胁传球"><span style={{ color: '#94a3b8' }}>{selectedTeam.xT?.toFixed(2)}</span></Descriptions.Item>
            </Descriptions>
            <ReactECharts option={teamCompareChart} style={{ height: 300, marginTop: 16 }} opts={{ renderer: 'svg' }} />
          </div>
        )}
      </Modal>
    </div>
  );
}

export default Teams;