/**
 * React应用主组件
 */

import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { ConfigProvider, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';

import Layout from './components/Layout';
import Home from './pages/Home';
import Predict from './pages/Predict';
import Teams from './pages/Teams';
import Players from './pages/Players';
import Matches from './pages/Matches';
import LiveStats from './pages/LiveStats';
import Analysis from './pages/Analysis';
import Login from './pages/Login';
import LeagueRules from './pages/LeagueRules';
import PredictionEvaluation from './pages/PredictionEvaluation';
import ModelOptimization from './pages/ModelOptimization';
import Bankroll from './pages/Bankroll';
import Parlay from './pages/Parlay';
import Professional from './pages/Professional';
import ModelManagement from './pages/ModelManagement';

function App() {
  return (
    <ConfigProvider 
      locale={zhCN} 
      theme={{
        algorithm: theme.darkAlgorithm,
        token: {
          colorPrimary: '#3b82f6',
          colorBgBase: '#1e293b',
          colorTextBase: '#f1f5f9',
          colorBgContainer: '#1e293b',
          colorBgElevated: '#334155',
          colorBorder: 'rgba(255, 255, 255, 0.05)',
        },
      }}
    >
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Home />} />
            <Route path="predict" element={<Predict />} />
            <Route path="teams" element={<Teams />} />
            <Route path="players" element={<Players />} />
            <Route path="matches" element={<Matches />} />
            <Route path="live" element={<LiveStats />} />
            <Route path="analysis" element={<Analysis />} />
            <Route path="league-rules" element={<LeagueRules />} />
            <Route path="evaluation" element={<PredictionEvaluation />} />
            <Route path="optimization" element={<ModelOptimization />} />
            <Route path="bankroll" element={<Bankroll />} />
            <Route path="parlay" element={<Parlay />} />
            <Route path="professional" element={<Professional />} />
            <Route path="login" element={<Login />} />
            <Route path="model-management" element={<ModelManagement />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  );
}

export default App;