"""
管理员密码设置工具
运行此脚本设置管理员密码，密码将使用 argon2id 加密后存入 admin_config.json
用法: python admin_setup.py
"""
import json
import os
import getpass
from argon2 import PasswordHasher, Type

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'admin_config.json')

def setup_admin():
    ph = PasswordHasher(type=Type.ID)  # type=Type.ID = argon2id

    print('=' * 40)
    print('  管理员密码设置工具')
    print('=' * 40)

    if os.path.exists(CONFIG_PATH):
        confirm = input('已存在管理员配置，是否覆盖？(y/N): ').strip().lower()
        if confirm != 'y':
            print('已取消。')
            return

    while True:
        password = getpass.getpass('请输入管理员密码: ')
        if len(password) < 6:
            print('密码至少6个字符，请重新输入。')
            continue
        password2 = getpass.getpass('请再次输入密码: ')
        if password != password2:
            print('两次密码不一致，请重新输入。')
            continue
        break

    hashed = ph.hash(password)
    config = {'admin_password_hash': hashed}

    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f'\n管理员密码已加密保存到 {CONFIG_PATH}')
    print(f'哈希值: {hashed}')
    print('请妥善保管配置文件。')


if __name__ == '__main__':
    setup_admin()
