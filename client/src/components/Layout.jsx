import React, { useState, useEffect } from 'react';
import { Outlet, Link, useLocation } from 'react-router-dom';
import { Layout as AntLayout, Menu, Button, Drawer, Badge, notification } from 'antd';
import {
  HomeOutlined,
  TrophyOutlined,
  TeamOutlined,
  CalendarOutlined,
  LineChartOutlined,
  MenuOutlined,
  MonitorOutlined,
  BookOutlined,
  BarChartOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  WalletOutlined,
  LinkOutlined,
  StarOutlined,
  ToolOutlined
} from '@ant-design/icons';

const { Header, Content, Footer, Sider } = AntLayout;

function Layout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [connected, setConnected] = useState(false);
  const location = useLocation();

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
      if (window.innerWidth >= 768) {
        setMobileMenuOpen(false);
      }
    };
    
    checkMobile();
    window.addEventListener('resize', checkMobile);
    
    let ws = null;
    let reconnectTimeout = null;
    let reconnectAttempts = 0;
    const maxReconnectAttempts = 10;
    
    const connectWebSocket = () => {
      try {
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsHost = window.location.host;
        ws = new WebSocket(`${wsProtocol}//${wsHost}/ws`);
        
        ws.onopen = () => {
          console.log('WebSocket连接成功');
          setConnected(true);
          reconnectAttempts = 0;
        };
        
        ws.onclose = (event) => {
          setConnected(false);
          
          const isNormalClose = event.code === 1000;
          const isAbnormalClose = event.code === 1006 || event.code === 1011;
          
          if (isNormalClose) {
            console.log('WebSocket正常关闭');
          } else if (isAbnormalClose) {
            console.error(`WebSocket异常关闭: code=${event.code}, reason=${event.reason}`);
          } else {
            console.log(`WebSocket连接关闭: code=${event.code}, reason=${event.reason}`);
          }
          
          if (!isNormalClose) {
            const delay = Math.min(Math.pow(2, reconnectAttempts) * 1000, 30000);
            console.log(`WebSocket重新连接中... (尝试 ${reconnectAttempts + 1}, 延迟 ${delay}ms)`);
            reconnectTimeout = setTimeout(connectWebSocket, delay);
            reconnectAttempts++;
          }
        };
        
        ws.onerror = (error) => {
          console.error('WebSocket错误:', error);
        };
        
        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (data.type === 'heartbeat') {
              ws.send(JSON.stringify({ type: 'heartbeat', timestamp: Date.now() }));
            } else if (data.type === 'odds_update') {
              // 赔率实时更新通知
              notification.info({
                message: '赔率实时更新',
                description: `${data.matchId || '比赛'}: 主胜 ${data.odds?.win || '-'} | 平局 ${data.odds?.draw || '-'} | 客胜 ${data.odds?.lose || '-'}`,
                placement: 'bottomRight',
                duration: 4,
              });
            } else if (data.type === 'prediction_result') {
              // 实时预测结果推送
              notification.success({
                message: '预测结果推送',
                description: data.message || `${data.matchId || '比赛'}预测已更新`,
                placement: 'bottomRight',
                duration: 5,
              });
            } else if (data.type === 'model_reload') {
              // 模型热更新通知
              notification.info({
                message: '模型热更新',
                description: `${data.key || '模型'}已${data.status === 'success' ? '成功' : '失败'}更新`,
                placement: 'bottomRight',
                duration: 4,
              });
            } else {
              console.log('WebSocket消息:', data);
            }
          } catch (e) {
            console.error('WebSocket消息解析失败:', e);
          }
        };
      } catch (e) {
        console.error('WebSocket连接初始化失败:', e);
        setConnected(false);
        
        reconnectTimeout = setTimeout(connectWebSocket, 5000);
        reconnectAttempts++;
      }
    };
    
    connectWebSocket();
    
    return () => {
      window.removeEventListener('resize', checkMobile);
      if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
      }
      if (ws) {
        ws.close();
      }
    };
  }, []);

  const menuItems = [
    { key: '/', icon: <HomeOutlined />, label: <Link to="/" onClick={() => setMobileMenuOpen(false)}>首页</Link> },
    { key: '/predict', icon: <TrophyOutlined />, label: <Link to="/predict" onClick={() => setMobileMenuOpen(false)}>比赛预测</Link> },
    { key: '/live', icon: <MonitorOutlined />, label: <Link to="/live" onClick={() => setMobileMenuOpen(false)}>实时赔率</Link> },
    { key: '/teams', icon: <TeamOutlined />, label: <Link to="/teams" onClick={() => setMobileMenuOpen(false)}>球队数据</Link> },
    { key: '/players', icon: <ThunderboltOutlined />, label: <Link to="/players" onClick={() => setMobileMenuOpen(false)}>球员统计</Link> },
    { key: '/matches', icon: <CalendarOutlined />, label: <Link to="/matches" onClick={() => setMobileMenuOpen(false)}>赛程安排</Link> },
    { key: '/analysis', icon: <LineChartOutlined />, label: <Link to="/analysis" onClick={() => setMobileMenuOpen(false)}>数据分析</Link> },
    { key: '/bankroll', icon: <WalletOutlined />, label: <Link to="/bankroll" onClick={() => setMobileMenuOpen(false)}>资金管理</Link> },
    { key: '/parlay', icon: <LinkOutlined />, label: <Link to="/parlay" onClick={() => setMobileMenuOpen(false)}>串关计算</Link> },
    { key: '/professional', icon: <StarOutlined />, label: <Link to="/professional" onClick={() => setMobileMenuOpen(false)}>专业投注</Link> },
    { key: '/league-rules', icon: <BookOutlined />, label: <Link to="/league-rules" onClick={() => setMobileMenuOpen(false)}>联赛规则</Link> },
    { key: '/evaluation', icon: <BarChartOutlined />, label: <Link to="/evaluation" onClick={() => setMobileMenuOpen(false)}>预测评估</Link> },
    { key: '/optimization', icon: <SettingOutlined />, label: <Link to="/optimization" onClick={() => setMobileMenuOpen(false)}>模型优化</Link> },
    { key: '/model-management', icon: <ToolOutlined />, label: <Link to="/model-management" onClick={() => setMobileMenuOpen(false)}>模型管理</Link> },
  ];

  return (
    <AntLayout style={{ minHeight: '100vh', background: 'linear-gradient(135deg, #0f172a 0%, #1e1b4b 50%, #0f172a 100%)' }}>
      {isMobile ? (
        <>
          <Drawer
            title="导航菜单"
            placement="left"
            closable={true}
            onClose={() => setMobileMenuOpen(false)}
            open={mobileMenuOpen}
            width={240}
            style={{ paddingTop: 64 }}
            styles={{ content: { background: '#1e293b', color: '#f1f5f9' } }}
          >
            <Menu 
              theme="dark" 
              mode="inline" 
              selectedKeys={[location.pathname]}
              items={menuItems}
              style={{ background: '#1e293b' }}
            />
          </Drawer>
          
          <AntLayout>
            <Header style={{ 
              padding: '0 16px', 
              background: 'rgba(30, 41, 59, 0.95)', 
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              boxShadow: '0 2px 20px rgba(0,0,0,0.3)',
              position: 'fixed',
              top: 0,
              left: 0,
              right: 0,
              zIndex: 1000,
              backdropFilter: 'blur(10px)'
            }}>
              <Button 
                type="text" 
                icon={<MenuOutlined />}
                onClick={() => setMobileMenuOpen(true)}
                style={{ fontSize: 20, color: '#f1f5f9' }}
              />
              <h2 style={{ margin: 0, fontSize: 16, fontWeight: 'bold', background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>五大联赛预测</h2>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Badge status={connected ? 'success' : 'error'} />
                <span style={{ fontSize: 12, color: connected ? '#34d399' : '#f87171' }}>{connected ? '在线' : '离线'}</span>
              </div>
            </Header>
            <Content style={{ 
              margin: '0', 
              padding: '80px 16px 24px', 
              background: 'transparent',
              minHeight: 'calc(100vh - 64px)'
            }}>
              <Outlet />
            </Content>
            <Footer style={{ textAlign: 'center', padding: '16px', color: '#64748b', background: 'rgba(30, 41, 59, 0.5)' }}>
              五大联赛足球预测模型 v8.0.0 ©2024
            </Footer>
          </AntLayout>
        </>
      ) : (
        <>
          <Sider 
            collapsible 
            collapsed={collapsed} 
            onCollapse={setCollapsed}
            theme="dark"
            breakpoint="lg"
            collapsedWidth="80"
            width={220}
            style={{ 
              background: 'rgba(30, 41, 59, 0.9)',
              backdropFilter: 'blur(10px)',
              borderRight: '1px solid rgba(255,255,255,0.05)'
            }}
          >
            <div style={{ 
              height: 48, 
              margin: 16, 
              background: 'linear-gradient(135deg, rgba(59, 130, 246, 0.2), rgba(139, 92, 246, 0.2))',
              borderRadius: 12,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              fontWeight: 'bold',
              fontSize: collapsed ? 20 : 14,
              border: '1px solid rgba(59, 130, 246, 0.3)'
            }}>
              {collapsed ? (
                <ThunderboltOutlined style={{ fontSize: 20 }} />
              ) : (
                <>
                  <ThunderboltOutlined style={{ fontSize: 16, marginRight: 8 }} />
                  五大联赛预测
                </>
              )}
            </div>
            <Menu 
              theme="dark" 
              mode="inline" 
              selectedKeys={[location.pathname]}
              items={menuItems}
              style={{ 
                background: 'transparent',
                borderRight: 'none'
              }}
            />
          </Sider>
          <AntLayout>
            <Header style={{ 
              padding: '0 24px', 
              background: 'rgba(30, 41, 59, 0.9)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              boxShadow: '0 2px 20px rgba(0,0,0,0.3)',
              backdropFilter: 'blur(10px)'
            }}>
              <div>
                <h2 style={{ margin: 0, fontSize: 20, fontWeight: 'bold', background: 'linear-gradient(90deg, #3b82f6, #8b5cf6, #10b981)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>五大联赛足球预测模型</h2>
                <p style={{ margin: '4px 0 0', fontSize: 12, color: '#94a3b8' }}>基于AI的智能足球比赛预测平台</p>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div className="status-dot" style={{ 
                    width: 10, 
                    height: 10, 
                    borderRadius: '50%',
                    backgroundColor: connected ? '#10b981' : '#ef4444',
                    animation: connected ? 'pulse 2s infinite' : 'none'
                  }}></div>
                  <span style={{ fontSize: 13, color: connected ? '#34d399' : '#f87171' }}>
                    {connected ? '实时连接正常' : '实时连接断开'}
                  </span>
                </div>
                <Button type="link" style={{ color: '#94a3b8' }}>
                  <Link to="/login">登录</Link>
                </Button>
              </div>
            </Header>
            <Content style={{ margin: '24px 16px', padding: 0, background: 'transparent' }}>
              <div style={{ 
                background: 'rgba(30, 41, 59, 0.6)', 
                borderRadius: 16, 
                padding: 24, 
                minHeight: 'calc(100vh - 180px)',
                border: '1px solid rgba(255,255,255,0.05)',
                backdropFilter: 'blur(10px)'
              }}>
                <Outlet />
              </div>
            </Content>
            <Footer style={{ textAlign: 'center', padding: '16px', color: '#64748b', background: 'rgba(30, 41, 59, 0.3)' }}>
              五大联赛足球预测模型 v8.0.0 ©2024 | 基于6模型集成的智能预测系统
            </Footer>
          </AntLayout>
        </>
      )}
      
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.4); }
          50% { opacity: 0.6; box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }
        }
        
        .ant-menu-dark .ant-menu-item:hover {
          background: rgba(59, 130, 246, 0.2);
        }
        
        .ant-layout-sider-trigger {
          background: rgba(59, 130, 246, 0.3) !important;
          color: #fff !important;
        }
        
        .ant-layout-sider-trigger:hover {
          background: rgba(59, 130, 246, 0.5) !important;
        }
      `}</style>
    </AntLayout>
  );
}

export default Layout;