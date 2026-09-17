/**
 * 球队数据更新脚本
 * 更新model-engine.js中的球队数据
 */

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

class TeamDataUpdater {

  /**
   * 安全解析 JS 对象字面量 (替代 eval，解决 C-003/D-003 安全漏洞)
   */
  _safeParseJsObject(objStr, label) {
    // 先尝试 JSON.parse
    try {
      return JSON.parse(objStr);
    } catch (_) {
      // JS 对象字面量 (单引号键名等)，使用 vm.Script 沙箱执行
      try {
        const sandbox = {};
        const script = new vm.Script(`this.value = ${objStr};`, {
          filename: `safe-parse-${label || 'unknown'}.js`
        });
        const context = vm.createContext(sandbox, {
          codeGeneration: { strings: false, wasm: false }
        });
        script.runInContext(context, { timeout: 1000 });
        return sandbox.value;
      } catch (vmErr) {
        throw new Error(`安全解析 ${label || 'JS对象'} 失败: ${vmErr.message}`);
      }
    }
  }
  constructor() {
    this.modelPath = path.join(__dirname, '../assets/model-engine.js');
  }

  /**
   * 更新球队数据
   */
  updateTeam(teamKey, updates) {
    console.log(`更新球队数据: ${teamKey}`);
    
    try {
      const content = fs.readFileSync(this.modelPath, 'utf8');
      
      // 解析TEAMS数据
      const teamsMatch = content.match(/var TEAMS = (\{[\s\S]*?\});/);
      if (!teamsMatch) {
        throw new Error('无法找到TEAMS数据');
      }

      const teams = this._safeParseJsObject(teamsMatch[1], 'TEAMS');

      if (!teams[teamKey]) {
        throw new Error(`球队 ${teamKey} 不存在`);
      }

      // 更新数据
      teams[teamKey] = {
        ...teams[teamKey],
        ...updates,
        updatedAt: new Date().toISOString()
      };

      // 重新构建文件内容
      const newTeamsCode = 'var TEAMS = ' + JSON.stringify(teams, null, 2) + ';';
      const newContent = content.replace(teamsMatch[0], newTeamsCode);

      // 保存文件
      fs.writeFileSync(this.modelPath, newContent, 'utf8');

      console.log(`✅ 球队 ${teamKey} 数据已更新`);
      return teams[teamKey];
    } catch (err) {
      console.error('❌ 更新失败:', err.message);
      throw err;
    }
  }

  /**
   * 批量更新球队数据
   */
  batchUpdate(updates) {
    console.log(`批量更新 ${Object.keys(updates).length} 支球队`);
    
    const results = {};
    for (const [teamKey, data] of Object.entries(updates)) {
      try {
        results[teamKey] = this.updateTeam(teamKey, data);
      } catch (err) {
        results[teamKey] = { error: err.message };
      }
    }

    console.log(`✅ 批量更新完成: ${Object.keys(results).length}支球队`);
    return results;
  }

  /**
   * 添加新球队
   */
  addTeam(teamKey, teamData) {
    console.log(`添加新球队: ${teamKey}`);
    
    try {
      const content = fs.readFileSync(this.modelPath, 'utf8');
      
      const teamsMatch = content.match(/var TEAMS = (\{[\s\S]*?\});/);
      if (!teamsMatch) {
        throw new Error('无法找到TEAMS数据');
      }

      const teams = this._safeParseJsObject(teamsMatch[1], 'TEAMS');

      if (teams[teamKey]) {
        throw new Error(`球队 ${teamKey} 已存在`);
      }

      // 添加新球队
      teams[teamKey] = {
        ...teamData,
        createdAt: new Date().toISOString()
      };

      // 重新构建文件内容
      const newTeamsCode = 'var TEAMS = ' + JSON.stringify(teams, null, 2) + ';';
      const newContent = content.replace(teamsMatch[0], newTeamsCode);

      // 保存文件
      fs.writeFileSync(this.modelPath, newContent, 'utf8');

      console.log(`✅ 球队 ${teamKey} 已添加`);
      return teams[teamKey];
    } catch (err) {
      console.error('❌ 添加失败:', err.message);
      throw err;
    }
  }

  /**
   * 更新球员数据
   */
  updatePlayer(teamKey, playerKey, playerData) {
    console.log(`更新球员数据: ${teamKey}.${playerKey}`);
    
    try {
      const content = fs.readFileSync(this.modelPath, 'utf8');
      
      // 解析PLAYER_POSTMATCH_RATINGS数据
      const playersMatch = content.match(/var PLAYER_POSTMATCH_RATINGS = (\{[\s\S]*?\});/);
      if (!playersMatch) {
        throw new Error('无法找到PLAYER_POSTMATCH_RATINGS数据');
      }

      let players;
      players = this._safeParseJsObject(playersMatch[1], 'PLAYER_POSTMATCH_RATINGS');

      if (!players[teamKey]) {
        players[teamKey] = {};
      }

      // 更新球员数据
      players[teamKey][playerKey] = {
        ...players[teamKey][playerKey],
        ...playerData,
        updatedAt: new Date().toISOString()
      };

      // 重新构建文件内容
      const newPlayersCode = 'var PLAYER_POSTMATCH_RATINGS = ' + JSON.stringify(players, null, 2) + ';';
      const newContent = content.replace(playersMatch[0], newPlayersCode);

      // 保存文件
      fs.writeFileSync(this.modelPath, newContent, 'utf8');

      console.log(`✅ 球员 ${teamKey}.${playerKey} 数据已更新`);
      return players[teamKey][playerKey];
    } catch (err) {
      console.error('❌ 更新失败:', err.message);
      throw err;
    }
  }

  /**
   * 批量更新球员数据
   */
  batchUpdatePlayers(teamKey, playersData) {
    console.log(`批量更新 ${teamKey} 球队 ${Object.keys(playersData).length} 名球员`);
    
    const results = {};
    for (const [playerKey, data] of Object.entries(playersData)) {
      try {
        results[playerKey] = this.updatePlayer(teamKey, playerKey, data);
      } catch (err) {
        results[playerKey] = { error: err.message };
      }
    }

    console.log(`✅ 批量更新完成`);
    return results;
  }

  /**
   * 获取所有球队列表
   */
  getTeamsList() {
    try {
      const content = fs.readFileSync(this.modelPath, 'utf8');
      const teamsMatch = content.match(/var TEAMS = (\{[\s\S]*?\});/);
      
      if (!teamsMatch) {
        throw new Error('无法找到TEAMS数据');
      }

      const teams = this._safeParseJsObject(teamsMatch[1], 'TEAMS');

      return Object.entries(teams).map(([key, team]) => ({
        key,
        name: team.name,
        group: team.group,
        fifaRank: team.fifaRank
      }));
    } catch (err) {
      console.error('❌ 获取球队列表失败:', err.message);
      return [];
    }
  }

  /**
   * 验证球队数据完整性
   */
  validateTeamData(teamKey) {
    const requiredFields = [
      'name', 'group', 'attack', 'defence', 'fifaRank',
      'xGOT', 'injury', 'keyPlayer', 'tactical', 'xT', 'xGA', 'xPTS',
      'tempo', 'pressIntensity', 'attackSide', 'marketValue', 'cohesion', 'recentForm'
    ];

    try {
      const content = fs.readFileSync(this.modelPath, 'utf8');
      const teamsMatch = content.match(/var TEAMS = (\{[\s\S]*?\});/);
      
      const teams = this._safeParseJsObject(teamsMatch[1], 'TEAMS');

      const team = teams[teamKey];
      if (!team) {
        return { valid: false, error: '球队不存在' };
      }

      const missingFields = requiredFields.filter(f => !team[f]);
      const invalidFields = [];

      // 验证数值范围
      if (team.attack < 0 || team.attack > 3) invalidFields.push('attack');
      if (team.defence < 0 || team.defence > 2) invalidFields.push('defence');
      if (team.fifaRank < 1 || team.fifaRank > 300) invalidFields.push('fifaRank');
      if (team.tempo < 0 || team.tempo > 2) invalidFields.push('tempo');

      return {
        valid: missingFields.length === 0 && invalidFields.length === 0,
        missingFields,
        invalidFields,
        team
      };
    } catch (err) {
      return { valid: false, error: err.message };
    }
  }

  /**
   * 从JSON文件导入球队数据
   */
  importFromJSON(filePath) {
    console.log(`从JSON导入数据: ${filePath}`);
    
    try {
      const jsonData = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      
      if (jsonData.teams) {
        this.batchUpdate(jsonData.teams);
      }
      
      if (jsonData.players) {
        for (const [teamKey, players] of Object.entries(jsonData.players)) {
          this.batchUpdatePlayers(teamKey, players);
        }
      }

      console.log('✅ JSON数据导入完成');
      return jsonData;
    } catch (err) {
      console.error('❌ JSON导入失败:', err.message);
      throw err;
    }
  }
}

// 主执行入口
const updater = new TeamDataUpdater();

// 命令行参数处理
const args = process.argv.slice(2);

if (args.includes('--list')) {
  const teams = updater.getTeamsList();
  console.log('球队列表:', teams);
} else if (args.includes('--validate')) {
  const teamKey = args[args.indexOf('--validate') + 1];
  const validation = updater.validateTeamData(teamKey);
  console.log('验证结果:', validation);
} else if (args.includes('--import')) {
  const filePath = args[args.indexOf('--import') + 1];
  updater.importFromJSON(filePath);
} else {
  console.log(`
球队数据更新脚本使用说明:

  node scripts/update-team-data.js --list                     获取球队列表
  node scripts/update-team-data.js --validate brazil          验证球队数据完整性
  node scripts/update-team-data.js --import data.json         从JSON导入数据

示例API调用（在代码中）:
  updater.updateTeam('brazil', { attack: 2.1 });
  updater.addTeam('newteam', { name: '新球队', group: 'A', ... });
  updater.updatePlayer('brazil', 'neymar', { rating: 7.8 });
`);
}

export default updater;