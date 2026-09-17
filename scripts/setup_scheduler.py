import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO_TRAIN_SCRIPT = os.path.join(PROJECT_ROOT, 'scripts', 'auto_train.py')
PYTHON_EXEC = sys.executable

def setup_windows_task():
    task_name = 'FootballModelAutoTrain'
    description = '每周一凌晨2点自动训练足球预测模型'
    
    trigger = '/tr "每周一凌晨2点"'
    start_time = '02:00:00'
    
    action = f'/sc weekly /d MON /st {start_time}'
    
    command = f'"{PYTHON_EXEC}" "{AUTO_TRAIN_SCRIPT}"'
    
    create_cmd = [
        'schtasks', '/create',
        '/tn', task_name,
        '/tr', command,
        '/sc', 'weekly',
        '/d', 'MON',
        '/st', start_time,
        '/ru', 'SYSTEM',
        '/f',
        '/rl', 'HIGHEST'
    ]
    
    print(f'创建定时任务命令: {" ".join(create_cmd)}')
    
    try:
        result = subprocess.run(create_cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print('✅ 定时任务创建成功')
            print(f'任务名称: {task_name}')
            print(f'执行时间: 每周一凌晨2:00')
            print(f'执行命令: {command}')
            
            list_cmd = ['schtasks', '/query', '/tn', task_name, '/v']
            list_result = subprocess.run(list_cmd, capture_output=True, text=True)
            print('\n任务详情:')
            print(list_result.stdout)
        else:
            print('❌ 定时任务创建失败')
            print(f'错误信息: {result.stderr}')
    except Exception as e:
        print(f'❌ 创建定时任务时发生异常: {e}')

def delete_existing_task():
    task_name = 'FootballModelAutoTrain'
    
    delete_cmd = ['schtasks', '/delete', '/tn', task_name, '/f']
    
    try:
        result = subprocess.run(delete_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f'已删除旧任务: {task_name}')
    except:
        pass

def main():
    print('========== 配置模型自动化训练定时任务 ==========')
    print(f'项目根目录: {PROJECT_ROOT}')
    print(f'训练脚本: {AUTO_TRAIN_SCRIPT}')
    print(f'Python路径: {PYTHON_EXEC}')
    print()
    
    delete_existing_task()
    setup_windows_task()
    
    print()
    print('========== 配置完成 ==========')
    print('训练周期: 每周一凌晨2:00')
    print('触发条件: 近7天有新比赛数据且距离上次训练超过7天')
    print('日志文件: logs/auto_train.log')

if __name__ == '__main__':
    main()