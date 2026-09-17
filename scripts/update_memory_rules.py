"""更新project_memory.md添加日志记录硬性规则"""
import os

def update_project_memory():
    """更新项目记忆文件"""
    file_path = r'c:\Users\Lenovo\.trae-cn\memory\projects\-g-zuqiu\project_memory.md'
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 添加新的日志规则
    new_rules = """| MEM-011 | 每次代码、参数、配置变更必须记录到change_log.md，包含变更ID、变更前后值、变更原因、验证结果和回滚方案 |
| MEM-012 | 每次任务完成后必须执行变更检查清单：变更前（原因/风险/回滚方案/备份）和变更后（记录/验证/文档更新/日志更新） |
| MEM-013 | 变更日志必须按类型分类记录：代码变更、参数变更、配置变更、数据变更、依赖变更、文档变更 |
| MEM-014 | 高风险变更（风险评估为高）必须先创建备份，并在测试环境验证通过后才能实施 |
| MEM-015 | 每次会话结束前必须确认：optimization_log.md已更新、change_log.md已更新、prompt_template.md已更新 |"""
    
    # 在MEM-010行之后添加新规则
    if 'MEM-010' in content:
        # 找到MEM-010行的末尾
        lines = content.split('\n')
        new_lines = []
        for line in lines:
            new_lines.append(line)
            if 'MEM-010' in line:
                new_lines.append(new_rules)
        
        content = '\n'.join(new_lines)
    
    # 更新文档版本信息
    content = content.replace('v2.1', 'v2.2')
    content = content.replace('2026-07-23', '2026-07-24')
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("project_memory.md 更新成功")
    print("新增规则：MEM-011 到 MEM-015")

if __name__ == '__main__':
    update_project_memory()